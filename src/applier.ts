import { isExcludedFromLiveUpdates } from './exclusions';
import { ILiveDocumentRegistry } from './tokens';

/**
 * Core of the applier plugin: given a `server_update` for `path`, reload the
 * matching open document from disk via `context.revert()` - unless the
 * document is either
 *
 * - excluded from live updates (e.g. notebooks, whose revert is unsafe - see
 *   `isExcludedFromLiveUpdates` in `exclusions.ts`), or
 * - dirty (has unsaved local changes), in which case we leave it alone and let
 *   JupyterLab's native save-conflict dialog surface the divergence when the
 *   user next saves.
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
  if (isExcludedFromLiveUpdates(widget)) {
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
