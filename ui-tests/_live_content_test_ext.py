# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Test-only server extension for the E2E watcher tests.

Exposes ``GET {base}/api/live-content/_test/watched`` returning the directories
the live-content manager is currently watching, relative to the server root.
This is enabled only by ``ui-tests/jupyter_server_test_config.py`` and is never
shipped with the package.
"""
from __future__ import annotations

import json
import os

from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.utils import url_path_join
from tornado import web

from jupyter_live_content.ws_api import MANAGER_SETTINGS_KEY


class WatchedDirsHandler(JupyterHandler):
    @web.authenticated
    def get(self) -> None:
        manager = self.settings.get(MANAGER_SETTINGS_KEY)
        if manager is None:
            self.finish(json.dumps({"watched": None}))
            return
        root = manager.root_dir
        watched = sorted(
            "." if d == root else os.path.relpath(d, root).replace(os.sep, "/")
            for d in manager.watched_dirs
        )
        self.finish(json.dumps({"watched": watched}))


def _load_jupyter_server_extension(server_app) -> None:
    web_app = server_app.web_app
    base_url = web_app.settings["base_url"]
    route = url_path_join(base_url, "api", "live-content", "_test", "watched")
    web_app.add_handlers(".*$", [(route, WatchedDirsHandler)])


def _jupyter_server_extension_points():
    return [{"module": "_live_content_test_ext"}]
