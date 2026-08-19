"""Server configuration for integration tests.

!! Never use this configuration in production because it
opens the server to the world and provide access to JupyterLab
JavaScript objects through the global window variable.
"""
import os

from jupyterlab.galata import configure_jupyter_server

configure_jupyter_server(c)  # noqa: F821

# Bind the exact port Playwright selected (see playwright.config.js). Default to
# 8989 so we never collide with a JupyterLab running on the usual 8888. Disable
# jupyter's own port auto-retry so the bound port matches what Playwright polls.
c.ServerApp.port = int(os.environ.get("TEST_PORT", "8989"))  # noqa: F821
c.ServerApp.port_retries = 0  # noqa: F821

# Uncomment to set server log level to debug level
# c.ServerApp.log_level = "DEBUG"
