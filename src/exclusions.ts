import { IDocumentWidget } from '@jupyterlab/docregistry';

/**
 * Decides whether an open document should be excluded from live-content
 * updates - that is, never reloaded from disk via `context.revert()` when the
 * underlying file changes.
 *
 * Notebooks are excluded. Reverting a notebook is unsafe:
 *
 * - `context.revert()` reloads the *entire* notebook from disk, discarding
 *   outputs, execution counts, cell IDs, and the widget/kernel association of
 *   the running session.
 * - Reliably reconciling an out-of-band notebook edit with in-memory state
 *   (so a benign change to one cell doesn't blow away the user's work in
 *   another) is an unsolved problem. See the discussion in
 *   jupyter-ai-contrib/jupyterlab-live-content#2 ("Update notebooks
 *   gracefully") and #5 ("Design: Incremental notebook updates").
 *
 * Until a notebook-aware, incremental update path exists, the safe default is
 * to leave notebooks untouched.
 */
export function isExcludedFromLiveUpdates(widget: IDocumentWidget): boolean {
  return isNotebook(widget);
}

/**
 * Returns `true` if the widget's document is a notebook.
 *
 * The primary signal is the Contents API content type. As a fallback we also
 * check the path extension, because `context.contentsModel` can be `null`
 * before a document has finished loading and we must never revert a notebook.
 */
function isNotebook(widget: IDocumentWidget): boolean {
  const context = widget.context;
  if (context.contentsModel?.type === 'notebook') {
    return true;
  }
  return context.path.toLowerCase().endsWith('.ipynb');
}
