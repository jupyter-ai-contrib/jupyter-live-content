# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Real-time-collaboration (RTC) provider detection.

``jupyterlab-live-content`` is a *poor-man's* live-content mechanism: it watches
the filesystem and reloads open documents from disk. When a real RTC provider is
active, live updates are already handled by the provider's shared-document
(YCRDT) transport, and running this extension on top would be redundant and can
fight the provider's own synchronization. In that case we disable ourselves.

This module answers one question for the current server session: **is an RTC
provider active?** It is adapted from ``jupyterlab_chat.rtc_lib`` (jupyter-chat);
we deliberately do *not* depend on jupyterlab-chat and borrow only the detection
logic.

Design notes
------------
* We never ``import`` an RTC provider or touch its internals. We only inspect the
  ``ServerApp`` to learn which server extensions are *installed*, which are
  *enabled* for this session, and -- for ``jupyter_server_ydoc`` -- whether RTC
  was turned off via the ``YDocExtension.disable_rtc`` trait.
* A provider is only *active* when enabled on two independent axes:
  ``enabled_by_server`` (the server extension is enabled) AND ``enabled_by_trait``
  (no trait disables it). ``jupyter_server_documents`` (JSD) has no disabling
  trait, so it is always trait-enabled; ``jupyter_server_ydoc`` (JSY) is
  trait-enabled unless ``disable_rtc`` is set.
* "Installed" (importable) is not the same as "enabled". An admin may ship an
  image with an RTC provider installed but disabled via
  ``jupyter server extension disable ...`` or the ``disable_rtc`` trait, and we
  must honor that: RTC stays off.
"""
from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from jupyter_server.serverapp import ServerApp

#: Backend server extensions that supply a shared-document (YCRDT) transport.
#: We only need their *names* -- never import them.
RTC_PROVIDERS = (
    "jupyter_server_documents",
    "jupyter_server_ydoc",
)

#: Traitlets application name under which ``jupyter_server_ydoc``'s
#: ``disable_rtc`` trait is addressed on ``ServerApp.config`` (fallback only).
_YDOC_APP_NAME = "YDocExtension"


def _is_installed(name: str) -> bool:
    """True iff module ``name`` is importable (installed in this environment)."""
    return importlib.util.find_spec(name) is not None


def _is_enabled(serverapp: "ServerApp", name: str) -> bool:
    """True iff ``name`` is a server extension configured AND enabled this session.

    Authoritative, unlike ``import name``: returns ``False`` when the package is
    installed but disabled via ``jupyter server extension disable``. Enablement is
    resolved from merged ``jpserver_extensions`` config before any extension
    loads, so this is safe to call from ``_load_jupyter_server_extension``.
    """
    ext = serverapp.extension_manager.extensions.get(name)
    return bool(ext and ext.enabled)


def _provider_app(serverapp: "ServerApp", name: str):
    """The live ``ExtensionApp`` instance for a provider, or ``None``.

    Both RTC providers sort alphabetically before ``jupyterlab_live_content``, so
    when a provider is enabled its app is already instantiated by the time we run.
    Never raises.
    """
    try:
        apps = getattr(serverapp.extension_manager, "extension_apps", None) or {}
        instances = apps.get(name)
        return next(iter(instances)) if instances else None
    except Exception:  # pragma: no cover - defensive; never break startup
        return None


def _coerce_bool(value: object) -> bool:
    """Coerce a config value to ``bool``.

    CLI-set extension traits can arrive in ``serverapp.config`` as a ``str``
    subclass (``DeferredConfigString``) before the owning class coerces them,
    e.g. ``--YDocExtension.disable_rtc=False`` stores ``'False'``. A naive
    ``bool('False')`` is ``True`` (non-empty string), which would wrongly report
    RTC as disabled.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def is_rtc_disabled_by_trait(serverapp: "ServerApp") -> bool:
    """True iff ``YDocExtension.disable_rtc`` is set for this session.

    Prefers the live ``jupyter_server_ydoc`` extension app (whose ``disable_rtc``
    is a properly coerced ``bool``), falling back to merged traitlets config with
    careful coercion. The traitlets default is ``False`` (RTC stays on). Governs
    only ``jupyter_server_ydoc``; ``jupyter_server_documents`` has no such trait.
    """
    app = _provider_app(serverapp, "jupyter_server_ydoc")
    if app is not None:
        return bool(getattr(app, "disable_rtc", False))
    ydoc_cfg = serverapp.config.get(_YDOC_APP_NAME, {})
    return _coerce_bool(ydoc_cfg.get("disable_rtc", False))


def get_rtc_provider(serverapp: "ServerApp") -> Optional[str]:
    """Name of the active RTC provider for this session, or ``None`` if RTC is off.

    A provider is active only when enabled on *both* axes: its server extension
    is enabled AND no trait disables it. If both providers are active,
    ``jupyter_server_documents`` (JSD) wins.
    """
    enabled_by_server = {name for name in RTC_PROVIDERS if _is_enabled(serverapp, name)}
    enabled_by_trait = {"jupyter_server_documents"}
    if not is_rtc_disabled_by_trait(serverapp):
        enabled_by_trait.add("jupyter_server_ydoc")

    active = enabled_by_server & enabled_by_trait
    if "jupyter_server_documents" in active:
        return "jupyter_server_documents"
    if "jupyter_server_ydoc" in active:
        return "jupyter_server_ydoc"
    return None


def is_rtc_active(serverapp: "ServerApp") -> bool:
    """Convenience wrapper: True iff any RTC provider is active this session."""
    return get_rtc_provider(serverapp) is not None
