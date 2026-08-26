# jupyterlab_live_content

[![Github Actions Status](https://github.com/jupyter-ai-contrib/jupyterlab-live-content/workflows/Build/badge.svg)](https://github.com/jupyter-ai-contrib/jupyterlab-live-content/actions/workflows/build.yml)

A JupyterLab extension that keeps open documents in sync with their file on
disk. When a file changes on disk while you have it open — for example, an AI
agent or another process rewrites it — this extension reloads the document in
the browser so what you see stays current, without requiring real-time
collaboration (RTC).

This extension is composed of a Python package named `jupyterlab_live_content`
for the server extension and a NPM package named `@jupyter-ai-contrib/live-content`
for the frontend extension.

## How it works

The server extension watches the content directory for filesystem changes and
notifies the browser over a WebSocket when a file you have open changes. The
frontend then reloads that document from disk (`context.revert()`), but only for
documents where a whole-file reload is safe.

Reloading is deliberately conservative. It is applied only to documents whose
view is a pure function of the file's bytes, so a reload cannot destroy
in-memory state. This is an allowlist: any document type not listed below is
left untouched rather than reloaded.

## What gets updated

| Document                                                                         | Updated live? | Notes                                                                                                                                                                                           |
| -------------------------------------------------------------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Text / code / Markdown in a **file editor** (`FileEditor`)                       | Yes           | Reloads the text buffer. Includes a notebook opened via **Open With → Editor**.                                                                                                                 |
| **Markdown preview** (`MarkdownViewer`)                                          | Yes           | Re-renders from the reloaded text.                                                                                                                                                              |
| **Images** (`ImageViewer`)                                                       | Yes           | Re-renders the image.                                                                                                                                                                           |
| A document with **unsaved changes** (dirty)                                      | No            | Your edits are never clobbered; JupyterLab's save-conflict dialog resolves the divergence when you next save.                                                                                   |
| **Notebooks** (the notebook view)                                                | No            | Reverting would discard outputs, execution counts, cell IDs, and the running-kernel association. This holds even for a read-only notebook, since read-only blocks saving but not running cells. |
| **Collaborative / RTC-backed** documents (JupyterGIS, JupyterCAD, chat files, …) | No            | The document's own provider owns synchronization.                                                                                                                                               |
| Any other / third-party document type                                            | No            | Excluded by default (allowlist).                                                                                                                                                                |

## Real-time collaboration (RTC)

If an RTC provider (`jupyter_server_ydoc` / Jupyter Collaboration, or
`jupyter_server_documents`) is installed **and** enabled, RTC already keeps open
documents in sync, so this extension disables itself entirely: the server does
not watch the filesystem or open a WebSocket, and the frontend does not connect.
If a provider is installed but disabled (via `jupyter server extension disable`
or the `disable_rtc` trait), this extension stays active.

## Future work

- **Notebooks.** Reliably reconciling an out-of-band notebook edit with live
  in-memory state (so a benign change to one cell doesn't blow away work in
  another) is unsolved. See the discussion in
  [#2](https://github.com/jupyter-ai-contrib/jupyterlab-live-content/issues/2)
  and [#5](https://github.com/jupyter-ai-contrib/jupyterlab-live-content/issues/5).
- **More viewer types.** Other read-only viewers (CSV/TSV, etc.) could be added
  to the allowlist as their reload behavior is validated.
- **PDFs.** JupyterLab 4 ships no built-in PDF document-widget viewer, so there
  is currently no PDF surface to update; a custom viewer would need to opt in.

## Requirements

- JupyterLab >= 4.0.0

## Install

To install the extension, execute:

```bash
pip install jupyterlab_live_content
```

## Uninstall

To remove the extension, execute:

```bash
pip uninstall jupyterlab_live_content
```

## Troubleshoot

If you are seeing the frontend extension, but it is not working, check
that the server extension is enabled:

```bash
jupyter server extension list
```

If the server extension is installed and enabled, but you are not seeing
the frontend extension, check the frontend extension is installed:

```bash
jupyter labextension list
```

## Contributing

If you would like to contribute to this extension, please refer to the [Contributing Guide](CONTRIBUTING.md).
