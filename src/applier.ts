import { isEligibleForLiveUpdate } from './eligibility';
import { ILiveDocumentRegistry } from './tokens';

/**
 * Core of the applier plugin: given a `server_update` for `path`, reload the
 * matching open document from disk via `context.revert()` - but only when
 *
 * - the document is eligible for live updates (a simple file editor or a
 *   read-only viewer - see `isEligibleForLiveUpdate` in `eligibility.ts`), and
 * - it is not dirty (has no unsaved local changes), so we never clobber the
 *   user's work; JupyterLab's native save-conflict dialog surfaces the
 *   divergence when the user next saves.
 *
 * Exported separately from the plugin wiring in `index.ts` so it can be
 * unit-tested without a running `JupyterFrontEnd`.
 */
export function applyServerUpdate(
  registry: ILiveDocumentRegistry,
  path: string
): void {
  const widget = registry.get(path);
  if (!widget) {
    return;
  }
  if (!isEligibleForLiveUpdate(widget)) {
    return;
  }
  const context = widget.context;
  if (context.model.dirty) {
    return;
  }
  context.revert().catch(err => {
    console.error(`live-content: failed to revert ${path}`, err);
  });
}
