import { IDocumentWidget } from '@jupyterlab/docregistry';
import { FileEditor } from '@jupyterlab/fileeditor';

/**
 * Allowlist: decide whether an open document is eligible to be reloaded from
 * disk (via `context.revert()`) when its file changes.
 *
 * Live-content updates are only applied where a blind, whole-file reload is
 * safe:
 *
 * - **Read-only documents** (`model.readOnly`): image / CSV / PDF / JSON viewers
 *   etc. There is no in-memory user state to clobber.
 * - **Simple file editors** (`FileEditor`): plain text / code / markdown, as
 *   long as they are *not* backed by a collaborative (RTC) model - a plain
 *   `revert()` just reloads the text buffer.
 *
 * Everything else is excluded, because reverting it is unsafe:
 *
 * - **Notebooks** - `revert()` discards outputs, execution counts, cell IDs, and
 *   the running-kernel association (see #2, #5).
 * - **Chat files** and other extension-owned documents whose canonical state is
 *   not the bytes on disk (see #3).
 * - **Collaborative / RTC-backed documents** (JupyterGIS, JupyterCAD, ...) whose
 *   own provider owns synchronization; a blind revert fights it.
 *
 * This is an allowlist by design: unknown/third-party document types are left
 * untouched rather than reloaded, which is the safe default.
 */
export function isEligibleForLiveUpdate(widget: IDocumentWidget): boolean {
  const model = widget.context.model;

  // Read-only viewers hold no editable state, so a reload can't lose anything.
  if (model.readOnly) {
    return true;
  }

  // Collaborative documents are synchronized by their RTC provider; never
  // revert them out from under it.
  if (model.collaborative) {
    return false;
  }

  // Otherwise, only plain file editors are safe to reload.
  return widget.content instanceof FileEditor;
}
