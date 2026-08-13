try:
    from ._version import __version__
except ImportError:
    # Fallback when using the package in dev mode without installing
    # in editable mode with pip. It is highly recommended to install
    # the package from a stable release or in editable mode: https://pip.pypa.io/en/stable/topics/local-project-installs/#editable-installs
    import warnings
    warnings.warn("Importing 'jupyterlab_live_content' outside a proper installation.")
    __version__ = "dev"
from .extension import _load_jupyter_server_extension

# Re-exported so `jupyter server extension` tooling can find the entrypoint.
__all__ = ["_load_jupyter_server_extension"]


def _jupyter_labextension_paths():
    return [{
        "src": "labextension",
        "dest": "@jupyter-ai-contrib/live-content"
    }]


def _jupyter_server_extension_points():
    return [{
        "module": "jupyterlab_live_content"
    }]
