# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""WebSocket API for ``jupyter-live-content``.

Two pieces live here:

``LiveContentManager``
    Owns the shared server-side state: the set of connected clients, the
    ``path -> {clients}`` subscription routing table, and a single background
    :func:`watchfiles.awatch` task. The watcher is scoped to *only* the
    directories of the documents clients currently have open, and re-scopes
    itself whenever a client opens, closes, or disconnects. When a watched file
    that some client has open changes on disk, the manager broadcasts a
    ``server_update`` to exactly the clients subscribed to that path.

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
import inspect
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

    def __init__(self, root_dir: str, log: "Logger", contents_manager=None) -> None:
        self.log = log
        # Used to compute the content hash of a changed file (same algorithm the
        # frontend sees in ``contentsModel.hash``), so a client can skip a
        # reload when disk already matches what it has.
        self._contents_manager = contents_manager
        # Resolve once so relative-path math against watcher events is stable.
        self.root_dir = os.path.realpath(root_dir)

        # All connected websocket handlers.
        self._clients: Set["LiveContentWebSocketHandler"] = set()
        # Routing table: api path -> set of clients that have it open.
        self._subscriptions: Dict[str, Set["LiveContentWebSocketHandler"]] = {}

        # Per-directory filesystem watchers. Each open document's directory gets
        # its own ``awatch`` task, so opening/closing a document in one
        # directory never interrupts the watchers for the others (no window
        # during which events could be missed).
        self._watching = False
        self._dir_tasks: Dict[str, "asyncio.Task"] = {}
        self._dir_stops: Dict[str, asyncio.Event] = {}

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
        self._refresh_watch()

    # -- subscription routing ----------------------------------------------

    def subscribe(self, client: "LiveContentWebSocketHandler", path: str) -> None:
        """Record that ``client`` has the document at ``path`` open."""
        self._subscriptions.setdefault(path, set()).add(client)
        self.log.debug("live-content: %s opened %r", id(client), path)
        self._refresh_watch()

    def unsubscribe(self, client: "LiveContentWebSocketHandler", path: str) -> None:
        """Record that ``client`` closed the document at ``path``."""
        subs = self._subscriptions.get(path)
        if subs is None:
            return
        subs.discard(client)
        if not subs:
            del self._subscriptions[path]
        self.log.debug("live-content: %s closed %r", id(client), path)
        self._refresh_watch()

    # -- broadcast ----------------------------------------------------------

    def broadcast_update(self, path: str, hash: Optional[str] = None) -> None:
        """Tell every client with ``path`` open that it changed on disk."""
        subs = self._subscriptions.get(path)
        if not subs:
            return
        payload = to_wire(ServerUpdate(path=path, hash=hash))
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

    @property
    def watched_dirs(self) -> Set[str]:
        """The directories the watcher monitors: the parent directory of every
        open document, and nothing else.

        A directory watch (rather than a per-file watch) is deliberate: editors
        and the Contents API frequently replace a file via delete+create, which
        would drop a watch bound to the file's inode. Watching the containing
        directory (non-recursively) still sees the change and keeps the scope
        minimal.
        """
        dirs: Set[str] = set()
        for path in self._subscriptions:
            abspath = os.path.realpath(os.path.join(self.root_dir, path))
            dirs.add(os.path.dirname(abspath))
        return dirs

    def ensure_watching(self) -> None:
        """Enable watching and start tasks for any already-open documents.

        Called on the first client connection to guarantee a running event
        loop, since ``_load_jupyter_server_extension`` may run before the IOLoop
        starts. Idempotent.
        """
        self._watching = True
        self._refresh_watch()

    def _refresh_watch(self) -> None:
        """Reconcile the running watcher tasks with ``watched_dirs``.

        Starts a watcher for any newly-needed directory and stops the watcher
        for any directory that no longer has an open document. Existing watchers
        are left untouched, so growing/shrinking the set never drops events for
        directories that remain watched. A no-op until :meth:`ensure_watching`
        has been called (and thus outside a running event loop, e.g. in unit
        tests).
        """
        if not self._watching:
            return
        desired = {d for d in self.watched_dirs if os.path.isdir(d)}
        for directory in list(self._dir_tasks):
            if directory not in desired:
                self._stop_dir(directory)
        for directory in desired:
            if directory not in self._dir_tasks:
                self._start_dir(directory)

    def _start_dir(self, directory: str) -> None:
        stop = asyncio.Event()
        self._dir_stops[directory] = stop
        self._dir_tasks[directory] = asyncio.ensure_future(
            self._watch_dir(directory, stop)
        )
        self.log.debug("live-content: watching %s", directory)

    def _stop_dir(self, directory: str) -> None:
        stop = self._dir_stops.pop(directory, None)
        if stop is not None:
            stop.set()
        task = self._dir_tasks.pop(directory, None)
        if task is not None:
            task.cancel()
        self.log.debug("live-content: stopped watching %s", directory)

    async def _watch_dir(self, directory: str, stop: asyncio.Event) -> None:
        """Watch a single directory (non-recursively) and broadcast changes to
        the open documents inside it."""
        try:
            async for changes in awatch(
                directory, recursive=False, stop_event=stop
            ):
                # Deduplicate raw events to api paths, and only broadcast for
                # paths a client actually has open.
                touched = set()
                for _change, abspath in changes:
                    api_path = self._to_api_path(abspath)
                    if api_path is not None and api_path in self._subscriptions:
                        touched.add(api_path)
                for api_path in touched:
                    hash = await self._content_hash(api_path)
                    self.broadcast_update(api_path, hash)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:  # noqa: BLE001
            self.log.exception("live-content: watcher for %s crashed", directory)

    def stop(self) -> None:
        """Stop all watchers. Safe to call if never started."""
        self._watching = False
        for directory in list(self._dir_tasks):
            self._stop_dir(directory)

    async def _content_hash(self, api_path: str) -> Optional[str]:
        """Content hash of the file at ``api_path`` per the ContentsManager, or
        ``None`` if unavailable (in which case the client reloads).

        Uses the same hash the frontend exposes as ``contentsModel.hash`` (e.g.
        sha256), so a client can compare it against the version it already has.
        """
        cm = self._contents_manager
        if cm is None:
            return None
        try:
            model = cm.get(api_path, content=False, require_hash=True)
            if inspect.isawaitable(model):
                model = await model
            return model.get("hash")
        except Exception:  # noqa: BLE001 - a deleted/unreadable file just reloads
            return None

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
