# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""WebSocket API for ``jupyterlab-live-content``.

Two pieces live here:

``LiveContentManager``
    Owns the shared server-side state: the set of connected clients, the
    ``path -> {clients}`` subscription routing table, and a single background
    :func:`watchfiles.awatch` task watching the server root directory. When a
    watched file that some client has open changes on disk, the manager
    broadcasts a ``server_update`` to exactly the clients subscribed to that
    path.

``LiveContentWebSocketHandler``
    A thin tornado WebSocket handler. It authenticates like any other Jupyter
    handler, registers/unregisters itself with the manager, and forwards
    parsed client messages (``client_opened`` / ``client_closed``) to the
    manager's routing table.

This is deliberately *not* RTC: there is no CRDT, no shared document, no merge.
It is a one-way "the bytes on disk changed, reload them" notification. The only
multi-client concern is routing, which is a plain dict of sets.
"""
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Dict, Optional, Set

from jupyter_server.auth.decorator import ws_authenticated
from jupyter_server.base.handlers import JupyterHandler
from tornado import websocket
from watchfiles import awatch

from .ws_schema import (
    ClientClosed,
    ClientOpened,
    ServerUpdate,
    parse_client_message,
    to_wire,
)

if TYPE_CHECKING:
    from logging import Logger


#: Key under which the manager is stored in ``web_app.settings``.
MANAGER_SETTINGS_KEY = "live_content_manager"


class LiveContentManager:
    """Tracks open files across clients and broadcasts on-disk changes."""

    def __init__(self, root_dir: str, log: "Logger") -> None:
        self.log = log
        # Resolve once so relative-path math against watcher events is stable.
        self.root_dir = os.path.realpath(root_dir)

        # All connected websocket handlers.
        self._clients: Set["LiveContentWebSocketHandler"] = set()
        # Routing table: api path -> set of clients that have it open.
        self._subscriptions: Dict[str, Set["LiveContentWebSocketHandler"]] = {}

        # Single background filesystem watcher task (started lazily).
        self._watch_task: Optional["asyncio.Task"] = None
        self._stop_event: Optional[asyncio.Event] = None

    # -- client lifecycle ---------------------------------------------------

    def add_client(self, client: "LiveContentWebSocketHandler") -> None:
        self._clients.add(client)
        self.log.debug("live-content: client connected (%d total)", len(self._clients))

    def remove_client(self, client: "LiveContentWebSocketHandler") -> None:
        self._clients.discard(client)
        # Drop the client from every path it was subscribed to.
        for path in list(self._subscriptions):
            subs = self._subscriptions.get(path)
            if subs is None:
                continue
            subs.discard(client)
            if not subs:
                del self._subscriptions[path]
        self.log.debug(
            "live-content: client disconnected (%d total)", len(self._clients)
        )

    # -- subscription routing ----------------------------------------------

    def subscribe(self, client: "LiveContentWebSocketHandler", path: str) -> None:
        """Record that ``client`` has the document at ``path`` open."""
        self._subscriptions.setdefault(path, set()).add(client)
        self.log.debug("live-content: %s opened %r", id(client), path)

    def unsubscribe(self, client: "LiveContentWebSocketHandler", path: str) -> None:
        """Record that ``client`` closed the document at ``path``."""
        subs = self._subscriptions.get(path)
        if subs is None:
            return
        subs.discard(client)
        if not subs:
            del self._subscriptions[path]
        self.log.debug("live-content: %s closed %r", id(client), path)

    # -- broadcast ----------------------------------------------------------

    def broadcast_update(self, path: str) -> None:
        """Tell every client with ``path`` open that it changed on disk."""
        subs = self._subscriptions.get(path)
        if not subs:
            return
        payload = to_wire(ServerUpdate(path=path))
        self.log.debug(
            "live-content: broadcasting update for %r to %d client(s)",
            path,
            len(subs),
        )
        # Copy: write_message may trigger a close -> mutate the set.
        for client in list(subs):
            try:
                client.write_message(payload)
            except Exception:  # noqa: BLE001 - a dead socket must not kill the loop
                self.log.warning(
                    "live-content: failed to send update to a client", exc_info=True
                )

    # -- filesystem watching ------------------------------------------------

    def ensure_watching(self) -> None:
        """Start the background watcher task if it is not already running.

        Started lazily (on the first client connection) to guarantee a running
        event loop, since ``_load_jupyter_server_extension`` may run before the
        IOLoop starts.
        """
        if self._watch_task is not None and not self._watch_task.done():
            return
        self._stop_event = asyncio.Event()
        self._watch_task = asyncio.ensure_future(self._watch())
        self.log.info("live-content: watching %s for changes", self.root_dir)

    async def _watch(self) -> None:
        try:
            async for changes in awatch(self.root_dir, stop_event=self._stop_event):
                # Deduplicate: several raw events can map to one api path.
                touched = set()
                for _change, abspath in changes:
                    api_path = self._to_api_path(abspath)
                    if api_path is not None:
                        touched.add(api_path)
                for api_path in touched:
                    if api_path in self._subscriptions:
                        self.broadcast_update(api_path)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:  # noqa: BLE001
            self.log.exception("live-content: file watcher crashed")

    def stop(self) -> None:
        """Signal the watcher to stop. Safe to call if never started."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None

    def _to_api_path(self, abspath: str) -> Optional[str]:
        """Convert an absolute filesystem path to a server API path.

        Returns ``None`` if the path is outside the server root. API paths are
        relative to the root and always use forward slashes, matching what the
        frontend sends via ``context.path``.
        """
        try:
            rel = os.path.relpath(os.path.realpath(abspath), self.root_dir)
        except ValueError:
            # e.g. different drive on Windows
            return None
        if rel == "." or rel.startswith(".."):
            return None
        return rel.replace(os.sep, "/")


class LiveContentWebSocketHandler(JupyterHandler, websocket.WebSocketHandler):
    """WebSocket endpoint at ``/api/live-content/ws``."""

    @property
    def manager(self) -> LiveContentManager:
        return self.settings[MANAGER_SETTINGS_KEY]

    @ws_authenticated
    async def get(self, *args, **kwargs):
        res = super().get(*args, **kwargs)
        if res is not None:
            await res

    def open(self) -> None:
        self.manager.add_client(self)
        self.manager.ensure_watching()

    def on_message(self, message: str) -> None:
        import json

        try:
            data = json.loads(message)
            parsed = parse_client_message(data)
        except (ValueError, json.JSONDecodeError) as e:
            self.log.warning("live-content: ignoring bad message %r (%s)", message, e)
            return

        if isinstance(parsed, ClientOpened):
            self.manager.subscribe(self, parsed.path)
        elif isinstance(parsed, ClientClosed):
            self.manager.unsubscribe(self, parsed.path)

    def on_close(self) -> None:
        self.manager.remove_client(self)
