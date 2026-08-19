import { IDocumentWidget } from '@jupyterlab/docregistry';

import { clientHasRevision, IRevision } from './revision';

/**
 * Reload a document widget from disk via `context.revert()`, unless the client
 * already holds that revision (its own save) or has unsaved local edits.
 *
 * Used for document types we do not update incrementally: plain text files, and
 * text-editor views of a notebook (raw JSON), where per-cell diffing does not
 * apply. In-place incremental updates are handled by NotebookLiveSync instead.
 */
export function coarseRevert(
  widget: IDocumentWidget,
  revision: IRevision
): void {
  const context = widget.context;
  if (clientHasRevision(context, revision)) {
    return;
  }
  if (context.model.dirty) {
    // Unsaved local changes: leave them; the native save-conflict dialog will
    // surface the divergence at save time.
    return;
  }
  context.revert().catch(err => {
    console.error(`live-content: failed to revert ${context.path}`, err);
  });
}
