# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Server extension entrypoint for ``jupyter-live-content``.

Wires up the :class:`~jupyter_live_content.ws_api.LiveContentManager` and
registers the WebSocket handler at ``/api/live-content/ws``.
"""
from __future__ import annotations

from jupyter_server.utils import url_path_join

from .rtc_lib import get_rtc_provider
from .ws_api import (
    MANAGER_SETTINGS_KEY,
    LiveContentManager,
    LiveContentWebSocketHandler,
)

#: URL namespace for this extension's handlers.
API_NAMESPACE = "api/live-content"

#: ``PageConfig`` key advertising whether the server disabled itself. The
#: frontend reads this to avoid opening a WebSocket that will never be served.
PAGE_CONFIG_DISABLED_KEY = "liveContentServerDisabled"


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

    If a real-time-collaboration (RTC) provider is active this session, the
    extension disables itself: RTC already delivers live updates via its shared
    document, so watching the filesystem and reverting on top would be redundant
    and could fight the provider. See :mod:`jupyter_live_content.rtc_lib`.

    Parameters
    ----------
    server_app: jupyter_server.serverapp.ServerApp
        The Jupyter server application instance.
    """
    web_app = server_app.web_app
    page_config = web_app.settings.setdefault("page_config_data", {})

    rtc_provider = get_rtc_provider(server_app)
    if rtc_provider is not None:
        page_config[PAGE_CONFIG_DISABLED_KEY] = True
        server_app.log.info(
            "jupyter_live_content: RTC provider %r is active; disabling "
            "live-content (RTC already provides live updates).",
            rtc_provider,
        )
        return

    page_config[PAGE_CONFIG_DISABLED_KEY] = False

    manager = LiveContentManager(
        root_dir=_get_root_dir(server_app),
        log=server_app.log,
        contents_manager=getattr(server_app, "contents_manager", None),
    )
    web_app.settings[MANAGER_SETTINGS_KEY] = manager

    base_url = web_app.settings["base_url"]
    ws_route = url_path_join(base_url, API_NAMESPACE, "ws")
    web_app.add_handlers(".*$", [(ws_route, LiveContentWebSocketHandler)])

    server_app.log.info("Registered jupyter_live_content server extension")
