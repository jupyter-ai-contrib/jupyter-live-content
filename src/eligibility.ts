import { IDocumentWidget } from '@jupyterlab/docregistry';
import { FileEditor } from '@jupyterlab/fileeditor';
import { ImageViewer } from '@jupyterlab/imageviewer';
import { MarkdownViewer } from '@jupyterlab/markdownviewer';

/**
 * Allowlist: decide whether an open document is eligible to be reloaded from
 * disk (via `context.revert()`) when its file changes.
 *
 * Live-content updates are only applied to widget types whose entire view is a
 * pure function of the file's bytes, so a blind reload can't destroy any
 * in-memory state:
 *
 * - **File editors** (`FileEditor`): plain text / code / markdown - `revert()`
 *   just reloads the text buffer. This also covers a notebook opened with
 *   "Open With -> Editor" (a `FileEditor` over the `.ipynb`).
 * - **Markdown previews** (`MarkdownViewer`): reverting re-renders the view.
 * - **Image viewers** (`ImageViewer`): reverting re-renders the image.
 *
 * ...and only when the document is not backed by a collaborative (RTC) model,
 * whose provider owns synchronization.
 *
 * Everything else is excluded, because reverting it is unsafe:
 *
 * - **Notebooks** (the `NotebookPanel` view) - `revert()` discards outputs,
 *   execution counts, cell IDs, and the running-kernel association (see #2, #5).
 *   This holds even for a read-only notebook file: read-only blocks *saving*,
 *   not running cells, so there is still live in-memory state to lose.
 * - **Chat files** and other extension-owned documents whose canonical state is
 *   not the bytes on disk (see #3).
 * - **Collaborative / RTC-backed documents** (JupyterGIS, JupyterCAD, ...).
 *
 * This is an allowlist by design: unknown/third-party document types are left
 * untouched rather than reloaded, which is the safe default.
 */
export function isEligibleForLiveUpdate(widget: IDocumentWidget): boolean {
  // Collaborative documents are synchronized by their RTC provider; never
  // revert them out from under it.
  if (widget.context.model.collaborative) {
    return false;
  }

  const content = widget.content;
  return (
    content instanceof FileEditor ||
    content instanceof MarkdownViewer ||
    content instanceof ImageViewer
  );
}
