import diffSequences from 'diff-sequences';

/**
 * Pure reconciliation planner. Given the client's current cell-id order and a
 * server target order (plus which cells changed and per-cell dirty/busy state),
 * it decides whether an update is reconcilable and, if so, what operations to
 * apply. No JupyterLab imports live here so it can be unit-tested in isolation.
 *
 * An update is reconcilable as a whole (atomic): if any operation would touch a
 * cell the user is editing (`isDirty`) or that is `busy`, the planner returns
 * `null` and the caller applies nothing, leaving JupyterLab's native
 * save-conflict dialog as the resolution path.
 */

export interface ICellHashes {
  source_hash: string;
  meta_hash: string;
}

export interface IReconcileInput {
  /** The client's current cell ids, in document order. */
  currentIds: string[];
  /** The server's target cell-id order. */
  targetOrder: string[];
  /** Hashes for the cells the update carries content for (id -> hashes). */
  changed: Record<string, ICellHashes>;
  /** The client's last-synced per-cell hashes (id -> hashes). */
  base: Record<string, ICellHashes>;
  /** Whether notebook-level metadata changed on disk. */
  nbMetaChanged: boolean;
  /** Whether the user has unsaved notebook-level metadata edits. */
  nbMetaDirty: boolean;
  /** Whether the user has unsaved edits in the given cell. */
  isDirty: (id: string) => boolean;
  /** Whether the given cell is executing or awaiting kernel input. */
  isBusy: (id: string) => boolean;
}

export interface IReconcilePlan {
  /** Cell ids to insert, in target order. Content comes from the update. */
  inserts: string[];
  /** Cell ids to delete. */
  deletes: string[];
  /** Surviving cell ids whose position changes. */
  moves: string[];
  /** Cell ids whose source should be rewritten. */
  sourceUpdates: string[];
  /** Cell ids whose metadata should be merged. */
  metaUpdates: string[];
  /** Whether to apply notebook-level metadata. */
  applyNbMetadata: boolean;
  /** The full target order to reconcile the document to. */
  targetOrder: string[];
}

/** Longest common subsequence of two id lists, via the Myers algorithm. */
export function longestCommonSubsequence(a: string[], b: string[]): string[] {
  const common: string[] = [];
  diffSequences(
    a.length,
    b.length,
    (aIndex: number, bIndex: number) => a[aIndex] === b[bIndex],
    (nCommon: number, aStart: number) => {
      for (let k = 0; k < nCommon; k++) {
        common.push(a[aStart + k]);
      }
    }
  );
  return common;
}

export function planReconcile(input: IReconcileInput): IReconcilePlan | null {
  const currentSet = new Set(input.currentIds);
  const targetSet = new Set(input.targetOrder);

  const deletes = input.currentIds.filter(id => !targetSet.has(id));
  const inserts = input.targetOrder.filter(id => !currentSet.has(id));

  // Surviving cells in each order, then the stable subsequence via LCS.
  const currentSurvivors = input.currentIds.filter(id => targetSet.has(id));
  const targetSurvivors = input.targetOrder.filter(id => currentSet.has(id));
  const stable = new Set(
    longestCommonSubsequence(currentSurvivors, targetSurvivors)
  );
  const moves = targetSurvivors.filter(id => !stable.has(id));

  // Content updates for surviving cells the update carries. We compare the
  // server's new hashes against the client's base hashes (both server-produced),
  // so no client-side hashing is needed.
  const sourceUpdates: string[] = [];
  const metaUpdates: string[] = [];
  for (const id of Object.keys(input.changed)) {
    if (!currentSet.has(id)) {
      continue; // an inserted cell; handled by `inserts`
    }
    const base = input.base[id];
    const next = input.changed[id];
    if (!base || base.source_hash !== next.source_hash) {
      sourceUpdates.push(id);
    }
    if (!base || base.meta_hash !== next.meta_hash) {
      metaUpdates.push(id);
    }
  }

  // Atomic reconcilability: every op that touches an existing cell must land on
  // a clean, idle cell. Inserts have nothing local to lose.
  const requiresClean = new Set<string>([
    ...deletes,
    ...moves,
    ...sourceUpdates,
    ...metaUpdates
  ]);
  for (const id of requiresClean) {
    if (input.isDirty(id) || input.isBusy(id)) {
      return null;
    }
  }
  if (input.nbMetaChanged && input.nbMetaDirty) {
    return null;
  }

  return {
    inserts,
    deletes,
    moves,
    sourceUpdates,
    metaUpdates,
    applyNbMetadata: input.nbMetaChanged,
    targetOrder: input.targetOrder
  };
}
