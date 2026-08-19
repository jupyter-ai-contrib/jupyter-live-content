# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""WebSocket API for ``jupyterlab-live-content``.

``LiveContentManager`` owns the shared server-side state: connected clients, the
``path -> {clients}`` routing table, a single :func:`watchfiles.awatch` task, and
the last-computed per-notebook manifest. When a watched file that some client has
open changes on disk, the manager either broadcasts an incremental ``nb_update``
(for notebooks, describing exactly which cells changed) or a coarse
``server_update`` (for other documents).

``LiveContentWebSocketHandler`` is a thin authenticated tornado WebSocket handler
that forwards parsed client messages to the manager.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from jupyter_server.auth.decorator import ws_authenticated
from jupyter_server.base.handlers import JupyterHandler
from tornado import websocket
from watchfiles import awatch

from . import nb_hash, nb_service
from .ws_schema import (
    ClientClosed,
    ClientOpened,
    FetchCells,
    GetManifest,
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

    def __init__(
        self, root_dir: str, log: "Logger", contents_manager: Any = None
    ) -> None:
        self.log = log
        self.contents_manager = contents_manager
        self.root_dir = os.path.realpath(root_dir)

        self._clients: Set["LiveContentWebSocketHandler"] = set()
        self._subscriptions: Dict[str, Set["LiveContentWebSocketHandler"]] = {}
        # Last-computed manifest per notebook path, for cheap change detection.
        self._manifests: Dict[str, nb_hash.NbManifest] = {}

        self._watch_task: Optional["asyncio.Task"] = None
        self._stop_event: Optional[asyncio.Event] = None

    # -- client lifecycle ---------------------------------------------------

    def add_client(self, client: "LiveContentWebSocketHandler") -> None:
        self._clients.add(client)

    def remove_client(self, client: "LiveContentWebSocketHandler") -> None:
        self._clients.discard(client)
        for path in list(self._subscriptions):
            subs = self._subscriptions.get(path)
            if subs is None:
                continue
            subs.discard(client)
            if not subs:
                del self._subscriptions[path]
                self._manifests.pop(path, None)

    # -- subscription routing ----------------------------------------------

    def subscribe(self, client: "LiveContentWebSocketHandler", path: str) -> None:
        self._subscriptions.setdefault(path, set()).add(client)
        if nb_service.is_notebook_path(path):
            asyncio.ensure_future(self._send_manifest(client, path))

    def unsubscribe(self, client: "LiveContentWebSocketHandler", path: str) -> None:
        subs = self._subscriptions.get(path)
        if subs is None:
            return
        subs.discard(client)
        if not subs:
            del self._subscriptions[path]
            self._manifests.pop(path, None)

    # -- client requests ----------------------------------------------------

    def handle_get_manifest(
        self, client: "LiveContentWebSocketHandler", path: str
    ) -> None:
        asyncio.ensure_future(self._send_manifest(client, path))

    def handle_fetch_cells(
        self, client: "LiveContentWebSocketHandler", path: str, ids: List[str]
    ) -> None:
        asyncio.ensure_future(self._send_cells(client, path, ids))

    # -- messaging ----------------------------------------------------------

    def _send(self, client: "LiveContentWebSocketHandler", message: Any) -> None:
        try:
            client.write_message(to_wire(message))
        except Exception:  # noqa: BLE001 - a dead socket must not kill the loop
            self.log.warning("live-content: failed to send to a client", exc_info=True)

    def broadcast_update(self, path: str) -> None:
        """Coarse ``server_update`` for non-notebook documents."""
        asyncio.ensure_future(self._broadcast_server_update(path))

    def _route(self, path: str, message: Any) -> None:
        """Send a message to every client subscribed to ``path``."""
        subs = self._subscriptions.get(path)
        if not subs:
            return
        for client in list(subs):
            self._send(client, message)

    async def _broadcast_server_update(self, path: str) -> None:
        if not self._subscriptions.get(path):
            return
        revision = {"last_modified": None, "hash": None, "hash_algorithm": None}
        if self.contents_manager is not None:
            revision = await nb_service.read_file_revision(
                self.contents_manager, path
            )
        self._route(
            path,
            ServerUpdate(
                path=path,
                last_modified=revision["last_modified"],
                hash=revision["hash"],
                hash_algorithm=revision["hash_algorithm"],
            ),
        )

    async def _send_manifest(
        self, client: "LiveContentWebSocketHandler", path: str
    ) -> None:
        result = await self._build_manifest(path)
        if result is None:
            return
        manifest, _nbcontent = result
        self._manifests[path] = manifest
        self._send(client, nb_service.manifest_message(path, manifest))

    async def _send_cells(
        self, client: "LiveContentWebSocketHandler", path: str, ids: List[str]
    ) -> None:
        result = await self._build_manifest(path)
        if result is None:
            return
        manifest, nbcontent = result
        self._manifests[path] = manifest
        self._send(client, nb_service.update_message(path, nbcontent, manifest, ids))

    async def _build_manifest(self, path: str):
        """Read the notebook and build its manifest. Returns None on failure."""
        if self.contents_manager is None:
            return None
        try:
            nbcontent, file_meta = await nb_service.read_notebook(
                self.contents_manager, path
            )
        except Exception:  # noqa: BLE001 - invalid/partial file: skip gracefully
            self.log.debug("live-content: could not read %r; skipping", path)
            return None
        return nb_service.build_manifest(nbcontent, file_meta), nbcontent

    # -- filesystem watching ------------------------------------------------

    def ensure_watching(self) -> None:
        if self._watch_task is not None and not self._watch_task.done():
            return
        self._stop_event = asyncio.Event()
        self._watch_task = asyncio.ensure_future(self._watch())
        self.log.info("live-content: watching %s for changes", self.root_dir)

    async def _watch(self) -> None:
        try:
            async for changes in awatch(self.root_dir, stop_event=self._stop_event):
                touched = set()
                for _change, abspath in changes:
                    api_path = self._to_api_path(abspath)
                    if api_path is not None:
                        touched.add(api_path)
                for api_path in touched:
                    if api_path in self._subscriptions:
                        await self._on_disk_change(api_path)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:  # noqa: BLE001
            self.log.exception("live-content: file watcher crashed")

    async def _on_disk_change(self, path: str) -> None:
        """Dispatch a change to a subscribed path."""
        if not nb_service.is_notebook_path(path):
            await self._broadcast_server_update(path)
            return

        result = await self._build_manifest(path)
        if result is None:
            # Invalid/partial notebook: skip, keep previous manifest, no broadcast.
            return
        manifest, nbcontent = result
        previous = self._manifests.get(path)
        diff = nb_hash.diff_manifests(previous, manifest)
        self._manifests[path] = manifest
        if diff.is_empty:
            return
        # changed = content-changed + newly-inserted ids (client tells them apart).
        message = nb_service.update_message(path, nbcontent, manifest, diff.changed)
        subs = self._subscriptions.get(path)
        if not subs:
            return
        for client in list(subs):
            self._send(client, message)

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None

    def _to_api_path(self, abspath: str) -> Optional[str]:
        try:
            rel = os.path.relpath(os.path.realpath(abspath), self.root_dir)
        except ValueError:
            return None
        if rel == "." or rel.startswith(".."):
            return None
        return rel.replace(os.sep, "/")


class LiveContentWebSocketHandler(JupyterHandler, websocket.WebSocketHandler):
    """WebSocket endpoint at ``/jupyterlab-live-content/ws``."""

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
        elif isinstance(parsed, GetManifest):
            self.manager.handle_get_manifest(self, parsed.path)
        elif isinstance(parsed, FetchCells):
            self.manager.handle_fetch_cells(self, parsed.path, parsed.ids)

    def on_close(self) -> None:
        self.manager.remove_client(self)
