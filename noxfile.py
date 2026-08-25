# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""nox sessions verifying that the live-content server extension disables itself
when a real-time-collaboration (RTC) provider is active.

Each session builds an isolated venv with a different RTC configuration and runs
``nox_tests/test_rtc_disable.py`` against it:

* ``rtc(none)``            -- no RTC provider              -> ENABLED
* ``rtc(ydoc_enabled)``    -- jupyter_server_ydoc enabled  -> DISABLED
* ``rtc(ydoc_disabled)``   -- ydoc installed but disabled  -> ENABLED
* ``rtc(ydoc_trait_off)``  -- ydoc enabled, disable_rtc set -> ENABLED
* ``rtc(jsd_enabled)``     -- jupyter_server_documents on   -> DISABLED

Run all with ``nox``; run one with e.g. ``nox -s "rtc(ydoc_enabled)"``.
"""
from __future__ import annotations

import nox

nox.options.default_venv_backend = "uv|virtualenv"

PYTHON = "3.12"
RTC_TEST = "nox_tests/test_rtc_disable.py"


def _install_extension(session: nox.Session) -> None:
    """Install the live-content extension (+ test deps) editably.

    The prebuilt labextension artifact is committed/kept in the source tree, so
    the jupyter-builder editable build hook is skipped (``skip-if-exists``); this
    keeps the RTC server tests fast and Node-free.
    """
    session.install("-e", ".[test]")


# Parametrized so each scenario shows up as its own session id in CI logs.
_SCENARIOS = {
    "none": dict(providers=[], expect_enabled=True, argv="", disable_ext=None),
    "ydoc_enabled": dict(
        providers=["jupyter-server-ydoc"], expect_enabled=False, argv="", disable_ext=None
    ),
    "ydoc_disabled": dict(
        providers=["jupyter-server-ydoc"],
        expect_enabled=True,
        argv="",
        disable_ext="jupyter_server_ydoc",
    ),
    "ydoc_trait_off": dict(
        providers=["jupyter-server-ydoc"],
        expect_enabled=True,
        argv="--YDocExtension.disable_rtc=True",
        disable_ext=None,
    ),
    "jsd_enabled": dict(
        providers=["jupyter-server-documents"], expect_enabled=False, argv="", disable_ext=None
    ),
}


@nox.session(python=PYTHON)
@nox.parametrize("scenario", [nox.param(name, id=name) for name in _SCENARIOS])
def rtc(session: nox.Session, scenario: str) -> None:
    cfg = _SCENARIOS[scenario]

    _install_extension(session)
    for provider in cfg["providers"]:
        session.install(provider)

    # Installed-but-not-enabled: disable the extension in this venv's sys.prefix.
    if cfg["disable_ext"]:
        session.run(
            "jupyter",
            "server",
            "extension",
            "disable",
            cfg["disable_ext"],
            "--sys-prefix",
        )

    session.env["LIVE_CONTENT_EXPECT_ENABLED"] = "1" if cfg["expect_enabled"] else "0"
    session.env["LIVE_CONTENT_ARGV"] = cfg["argv"]

    session.run("pytest", "-q", RTC_TEST)
