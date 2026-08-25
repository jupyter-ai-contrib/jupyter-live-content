# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Unit tests for :mod:`jupyterlab_live_content.rtc_lib`.

These exercise the detection logic against a *fake* ``ServerApp`` so they run in
the default suite without installing any real RTC provider. The end-to-end
behavior with real providers installed (and enabled/disabled) is covered by the
``nox`` sessions in ``noxfile.py``.
"""
from __future__ import annotations

from jupyterlab_live_content.rtc_lib import get_rtc_provider, is_rtc_active


class _FakeExtension:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled


class _FakeExtensionManager:
    def __init__(self, extensions: dict) -> None:
        self.extensions = extensions
        self.extension_apps: dict = {}


class _FakeServerApp:
    """Mimics the surface of ``ServerApp`` that ``rtc_lib`` inspects."""

    def __init__(self, *, ext_states: dict | None = None, config: dict | None = None):
        # ext_states maps extension name -> enabled(bool). Absent => not present.
        self.extension_manager = _FakeExtensionManager(
            {name: _FakeExtension(enabled) for name, enabled in (ext_states or {}).items()}
        )
        self.config = config or {}


def test_no_rtc_provider():
    app = _FakeServerApp()
    assert get_rtc_provider(app) is None
    assert is_rtc_active(app) is False


def test_ydoc_enabled_is_active():
    app = _FakeServerApp(ext_states={"jupyter_server_ydoc": True})
    assert get_rtc_provider(app) == "jupyter_server_ydoc"
    assert is_rtc_active(app) is True


def test_jsd_enabled_is_active():
    app = _FakeServerApp(ext_states={"jupyter_server_documents": True})
    assert get_rtc_provider(app) == "jupyter_server_documents"


def test_both_enabled_jsd_wins():
    app = _FakeServerApp(
        ext_states={"jupyter_server_documents": True, "jupyter_server_ydoc": True}
    )
    assert get_rtc_provider(app) == "jupyter_server_documents"


def test_ydoc_installed_but_disabled_is_inactive():
    # Installed (present in extension_manager) but enabled=False.
    app = _FakeServerApp(ext_states={"jupyter_server_ydoc": False})
    assert get_rtc_provider(app) is None


def test_ydoc_enabled_but_disable_rtc_trait_is_inactive():
    app = _FakeServerApp(
        ext_states={"jupyter_server_ydoc": True},
        config={"YDocExtension": {"disable_rtc": True}},
    )
    assert get_rtc_provider(app) is None


def test_disable_rtc_trait_as_deferred_string_true():
    # CLI-set trait can arrive as a string; "True" must disable RTC.
    app = _FakeServerApp(
        ext_states={"jupyter_server_ydoc": True},
        config={"YDocExtension": {"disable_rtc": "True"}},
    )
    assert get_rtc_provider(app) is None


def test_disable_rtc_trait_as_deferred_string_false_keeps_rtc():
    # "False" as a string must NOT be treated as truthy.
    app = _FakeServerApp(
        ext_states={"jupyter_server_ydoc": True},
        config={"YDocExtension": {"disable_rtc": "False"}},
    )
    assert get_rtc_provider(app) == "jupyter_server_ydoc"


def test_disable_rtc_trait_does_not_affect_jsd():
    # disable_rtc only governs JSY; JSD stays active.
    app = _FakeServerApp(
        ext_states={"jupyter_server_documents": True},
        config={"YDocExtension": {"disable_rtc": True}},
    )
    assert get_rtc_provider(app) == "jupyter_server_documents"
