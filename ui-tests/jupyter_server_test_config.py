"""Server configuration for integration tests.

!! Never use this configuration in production because it
opens the server to the world and provide access to JupyterLab
JavaScript objects through the global window variable.
"""
from jupyterlab.galata import configure_jupyter_server

configure_jupyter_server(c)

# Enable the E2E-only test extension (ui-tests/_live_content_test_ext.py), which
# exposes the manager's watched directories for server-watcher.spec.ts.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
c.ServerApp.jpserver_extensions = {
    "jupyter_live_content": True,
    "_live_content_test_ext": True,
}

# Uncomment to set server log level to debug level
# c.ServerApp.log_level = "DEBUG"
