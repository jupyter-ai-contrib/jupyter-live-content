# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""End-to-end RTC-disable test, driven by :mod:`noxfile`.

Each ``nox`` session installs a different combination of RTC providers (and
enables/disables them), then runs THIS module. The session communicates the
scenario via environment variables:

* ``LIVE_CONTENT_EXPECT_ENABLED`` -- ``"1"`` if the live-content server extension
  should stay enabled (RTC not active), ``"0"`` if it should disable itself.
* ``LIVE_CONTENT_ARGV`` -- optional space-separated CLI args passed to
  ``ServerApp.initialize`` (e.g. ``--YDocExtension.disable_rtc=True``).

We initialize a real ``ServerApp`` in-process (``new_httpserver=False`` so no
port is bound). ``initialize`` runs the full extension discovery + load path,
including our ``_load_jupyter_server_extension`` and any installed RTC provider,
so the assertion reflects real behavior for the venv's installed packages.
"""
from __future__ import annotations

import os

import pytest
from jupyter_server.serverapp import ServerApp

from jupyterlab_live_content.extension import PAGE_CONFIG_DISABLED_KEY
from jupyterlab_live_content.ws_api import MANAGER_SETTINGS_KEY

# Only meaningful when a nox session has set up the scenario. Skipped in the
# default `pytest` run (which has no RTC provider configured).
pytestmark = pytest.mark.skipif(
    "LIVE_CONTENT_EXPECT_ENABLED" not in os.environ,
    reason="RTC scenario env not set; this test is driven by noxfile.py",
)


def _load_serverapp() -> ServerApp:
    ServerApp.clear_instance()
    app = ServerApp()
    argv = os.environ.get("LIVE_CONTENT_ARGV", "").split()
    app.initialize(argv=argv, new_httpserver=False)
    return app


def test_live_content_enablement_matches_rtc_state():
    expect_enabled = os.environ["LIVE_CONTENT_EXPECT_ENABLED"] == "1"

    app = _load_serverapp()
    settings = app.web_app.settings

    # The manager (and WS handler) is only registered when we stay enabled.
    manager_registered = MANAGER_SETTINGS_KEY in settings
    assert manager_registered is expect_enabled, (
        f"expected live-content enabled={expect_enabled}, "
        f"but manager_registered={manager_registered}"
    )

    # The PageConfig flag is the frontend-facing signal and must be the inverse.
    disabled_flag = settings.get("page_config_data", {}).get(PAGE_CONFIG_DISABLED_KEY)
    assert disabled_flag is (not expect_enabled), (
        f"expected liveContentServerDisabled={not expect_enabled}, "
        f"but flag={disabled_flag!r}"
    )
