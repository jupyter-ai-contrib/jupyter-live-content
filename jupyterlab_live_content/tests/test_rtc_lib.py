# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Unit tests for :mod:`jupyterlab_live_content.rtc_lib`.

These exercise the detection logic against a *fake* ``ServerApp`` so they run in
the default suite without installing any real RTC provider. The end-to-end
behavior with real providers installed is covered by the E2E ``rtc`` suite.
"""
from __future__ import annotations

from jupyterlab_live_content.rtc_lib import get_rtc_provider, is_rtc_active


class _Ext:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled


class _YDocApp:
    def __init__(self, disable_rtc: bool) -> None:
        self.disable_rtc = disable_rtc


class _FakeServerApp:
    """Mimics the ``ServerApp`` surface that ``rtc_lib`` inspects."""

    def __init__(self, *, enabled=(), ydoc_disable_rtc: bool | None = None):
        mgr = type("_Mgr", (), {})()
        mgr.extensions = {name: _Ext(True) for name in enabled}
        mgr.extension_apps = {}
        if ydoc_disable_rtc is not None:
            mgr.extension_apps["jupyter_server_ydoc"] = {_YDocApp(ydoc_disable_rtc)}
        self.extension_manager = mgr


def test_no_rtc_provider():
    app = _FakeServerApp()
    assert get_rtc_provider(app) is None
    assert is_rtc_active(app) is False


def test_ydoc_enabled_is_active():
    assert get_rtc_provider(_FakeServerApp(enabled=["jupyter_server_ydoc"])) == (
        "jupyter_server_ydoc"
    )


def test_jsd_enabled_is_active():
    assert get_rtc_provider(_FakeServerApp(enabled=["jupyter_server_documents"])) == (
        "jupyter_server_documents"
    )


def test_both_enabled_jsd_wins():
    app = _FakeServerApp(enabled=["jupyter_server_documents", "jupyter_server_ydoc"])
    assert get_rtc_provider(app) == "jupyter_server_documents"


def test_ydoc_installed_but_disabled_is_inactive():
    # Present in extension_manager but enabled=False (e.g. `jupyter server
    # extension disable jupyter_server_ydoc`).
    app = _FakeServerApp()
    app.extension_manager.extensions["jupyter_server_ydoc"] = _Ext(False)
    assert get_rtc_provider(app) is None


def test_ydoc_disable_rtc_trait_is_inactive():
    app = _FakeServerApp(enabled=["jupyter_server_ydoc"], ydoc_disable_rtc=True)
    assert get_rtc_provider(app) is None


def test_disable_rtc_trait_does_not_affect_jsd():
    app = _FakeServerApp(
        enabled=["jupyter_server_documents"], ydoc_disable_rtc=True
    )
    assert get_rtc_provider(app) == "jupyter_server_documents"
