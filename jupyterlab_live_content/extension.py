# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Server extension entrypoint for ``jupyterlab-live-content``.

Wires up the :class:`~jupyterlab_live_content.ws_api.LiveContentManager` and
registers the WebSocket handler at ``/jupyterlab-live-content/ws``.
"""
from __future__ import annotations

from jupyter_server.utils import url_path_join

from .ws_api import (
    MANAGER_SETTINGS_KEY,
    LiveContentManager,
    LiveContentWebSocketHandler,
)

#: URL namespace for this extension's handlers.
API_NAMESPACE = "jupyterlab-live-content"


def _get_root_dir(server_app) -> str:
    """Best-effort resolution of the server's content root directory.

    ``_load_jupyter_server_extension`` may be handed either a ``ServerApp`` or a
    ``LabApp`` depending on how the extension is loaded, so we probe both.
    """
    root = getattr(server_app, "root_dir", None)
    if not root:
        serverapp = getattr(server_app, "serverapp", None)
        root = getattr(serverapp, "root_dir", None)
    if not root:
        import os

        root = os.getcwd()
    return root


def _load_jupyter_server_extension(server_app) -> None:
    """Registers the WebSocket handler and starts the file watcher.

    Parameters
    ----------
    server_app: jupyter_server.serverapp.ServerApp
        The Jupyter server application instance.
    """
    web_app = server_app.web_app

    contents_manager = getattr(server_app, "contents_manager", None)
    if contents_manager is None:
        contents_manager = web_app.settings.get("contents_manager")

    manager = LiveContentManager(
        root_dir=_get_root_dir(server_app),
        log=server_app.log,
        contents_manager=contents_manager,
    )
    web_app.settings[MANAGER_SETTINGS_KEY] = manager

    base_url = web_app.settings["base_url"]
    ws_route = url_path_join(base_url, API_NAMESPACE, "ws")
    web_app.add_handlers(".*$", [(ws_route, LiveContentWebSocketHandler)])

    server_app.log.info("Registered jupyterlab_live_content server extension")
