# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Detect whether a real-time-collaboration (RTC) provider is active this session.

When one is, jupyterlab-live-content disables itself (RTC already keeps open
documents in sync). Adapted from ``jupyterlab_chat.rtc_lib``: we only inspect
the ``ServerApp`` and never import a provider.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from jupyter_server.serverapp import ServerApp

#: Server extensions providing the shared-document (YCRDT) transport.
RTC_PROVIDERS = ("jupyter_server_documents", "jupyter_server_ydoc")


def get_rtc_provider(serverapp: "ServerApp") -> Optional[str]:
    """Name of the active RTC provider this session, or ``None`` if RTC is off."""
    manager = serverapp.extension_manager
    apps = getattr(manager, "extension_apps", {}) or {}
    for name in RTC_PROVIDERS:
        ext = manager.extensions.get(name)
        if not (ext and ext.enabled):
            continue
        # jupyter_server_ydoc can be enabled yet have RTC turned off via its
        # ``disable_rtc`` trait; its app exists here since it's enabled.
        if name == "jupyter_server_ydoc":
            app = next(iter(apps.get(name) or ()), None)
            if getattr(app, "disable_rtc", False):
                continue
        return name
    return None


def is_rtc_active(serverapp: "ServerApp") -> bool:
    return get_rtc_provider(serverapp) is not None
