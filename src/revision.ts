import { DocumentRegistry } from '@jupyterlab/docregistry';

/** File-revision metadata carried by server updates. */
export interface IRevision {
  last_modified: string | null;
  hash: string | null;
  hash_algorithm: string | null;
}

/**
 * Whether the client already holds the on-disk revision an update describes.
 *
 * This is how a client tells its own save apart from a genuine out-of-band
 * change. After a save, `context.contentsModel` records the saved revision's
 * hash; the server's echo of that same write carries the same hash, so we can
 * recognize it and skip the update entirely (no reload, no popup).
 *
 * Prefers the content hash (as `Context._maybeSave` does); falls back to
 * `last_modified` when no hash is available.
 */
export function clientHasRevision(
  context: DocumentRegistry.IContext<DocumentRegistry.IModel>,
  revision: IRevision
): boolean {
  const recorded = context.contentsModel;
  if (!recorded) {
    return false;
  }
  if (revision.hash && recorded.hash) {
    return revision.hash === recorded.hash;
  }
  if (revision.last_modified && recorded.last_modified) {
    // The update is not newer than what we already recorded.
    return (
      new Date(revision.last_modified).getTime() <=
      new Date(recorded.last_modified).getTime()
    );
  }
  return false;
}
