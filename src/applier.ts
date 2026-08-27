import { isEligibleForLiveUpdate } from './eligibility';
import { ILiveDocumentRegistry } from './tokens';

/**
 * Core of the applier plugin: given a `server_update` for `path`, reload the
 * matching open document from disk via `context.revert()` - but only when
 *
 * - the document is eligible for live updates (a simple file editor, markdown
 *   preview, or image viewer - see `isEligibleForLiveUpdate`), and
 * - it is not dirty (has no unsaved local changes), and
 * - the on-disk content actually differs from what the client already has.
 *
 * The last check uses the ContentsManager content hash: `hash` is the server's
 * hash of the file after the change, and `context.contentsModel.hash` is the
 * hash of the version this client last loaded or saved. When they match, disk
 * and model are already in sync (e.g. the client just saved), so reloading
 * would be a redundant self-revert and is skipped. If the server sent no hash,
 * we reload to be safe.
 *
 * Exported separately from the plugin wiring in `index.ts` so it can be
 * unit-tested without a running `JupyterFrontEnd`.
 */
export function applyServerUpdate(
  registry: ILiveDocumentRegistry,
  path: string,
  hash?: string | null
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
  if (hash && context.contentsModel?.hash === hash) {
    // Disk already matches what this client has (e.g. our own save).
    return;
  }
  context.revert().catch(err => {
    console.error(`live-content: failed to revert ${path}`, err);
  });
}
