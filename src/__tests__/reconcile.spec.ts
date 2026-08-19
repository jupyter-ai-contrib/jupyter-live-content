import {
  longestCommonSubsequence,
  planReconcile,
  IReconcileInput
} from '../reconcile';

const H = (s: string, m = 'm'): { source_hash: string; meta_hash: string } => ({
  source_hash: s,
  meta_hash: m
});

function base(input: Partial<IReconcileInput>): IReconcileInput {
  return {
    currentIds: [],
    targetOrder: [],
    changed: {},
    base: {},
    nbMetaChanged: false,
    nbMetaDirty: false,
    isDirty: () => false,
    isBusy: () => false,
    ...input
  };
}

describe('longestCommonSubsequence', () => {
  it('keeps the stable subsequence when items are inserted', () => {
    expect(
      longestCommonSubsequence(['a', 'b', 'c'], ['a', 'b', 'x', 'y', 'c'])
    ).toEqual(['a', 'b', 'c']);
  });

  it('finds the maximal stable set under a reorder', () => {
    // Moving 'd' to the front leaves [a,b,c] stable.
    expect(
      longestCommonSubsequence(['a', 'b', 'c', 'd'], ['d', 'a', 'b', 'c'])
    ).toEqual(['a', 'b', 'c']);
  });
});

describe('planReconcile', () => {
  it('applies a clean single-cell source change', () => {
    const plan = planReconcile(
      base({
        currentIds: ['c1', 'c2'],
        targetOrder: ['c1', 'c2'],
        base: { c1: H('s1'), c2: H('s2') },
        changed: { c2: H('s2new') }
      })
    );
    expect(plan).not.toBeNull();
    expect(plan!.sourceUpdates).toEqual(['c2']);
    expect(plan!.metaUpdates).toEqual([]);
    expect(plan!.moves).toEqual([]);
    expect(plan!.inserts).toEqual([]);
    expect(plan!.deletes).toEqual([]);
  });

  it('detects a metadata-only change without a source update', () => {
    const plan = planReconcile(
      base({
        currentIds: ['c1'],
        targetOrder: ['c1'],
        base: { c1: H('s1', 'm1') },
        changed: { c1: H('s1', 'm2') }
      })
    );
    expect(plan!.sourceUpdates).toEqual([]);
    expect(plan!.metaUpdates).toEqual(['c1']);
  });

  it('plans inserts and deletes from the target order', () => {
    const plan = planReconcile(
      base({
        currentIds: ['a', 'b', 'c'],
        targetOrder: ['a', 'x', 'c'],
        base: { a: H('a'), b: H('b'), c: H('c') },
        changed: { x: H('x') }
      })
    );
    expect(plan!.inserts).toEqual(['x']);
    expect(plan!.deletes).toEqual(['b']);
  });

  it('rejects the whole update if it touches a dirty cell', () => {
    const plan = planReconcile(
      base({
        currentIds: ['c1', 'c2'],
        targetOrder: ['c1', 'c2'],
        base: { c1: H('s1'), c2: H('s2') },
        changed: { c2: H('s2new') },
        isDirty: id => id === 'c2'
      })
    );
    expect(plan).toBeNull();
  });

  it('rejects the whole update if it touches a busy cell', () => {
    const plan = planReconcile(
      base({
        currentIds: ['c1'],
        targetOrder: ['c1'],
        base: { c1: H('s1') },
        changed: { c1: H('s1new') },
        isBusy: id => id === 'c1'
      })
    );
    expect(plan).toBeNull();
  });

  it('reports moves for displaced surviving cells only', () => {
    const plan = planReconcile(
      base({
        currentIds: ['a', 'b', 'c', 'd'],
        targetOrder: ['d', 'a', 'b', 'c'],
        base: { a: H('a'), b: H('b'), c: H('c'), d: H('d') }
      })
    );
    // a, b, c are the stable subsequence; only d moves.
    expect(plan!.moves).toEqual(['d']);
  });

  it('applies notebook metadata when clean', () => {
    const plan = planReconcile(base({ nbMetaChanged: true }));
    expect(plan!.applyNbMetadata).toBe(true);
  });

  it('rejects when notebook metadata changed but is locally dirty', () => {
    const plan = planReconcile(
      base({ nbMetaChanged: true, nbMetaDirty: true })
    );
    expect(plan).toBeNull();
  });
});
