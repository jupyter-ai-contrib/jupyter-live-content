import { IDocumentWidget } from '@jupyterlab/docregistry';

import { applyServerUpdate } from '../applier';
import { ILiveDocumentRegistry } from '../tokens';

/**
 * Build a fake `IDocumentWidget` exposing only what `applyServerUpdate` reads,
 * plus a jest mock for `context.revert` so we can assert whether a reload was
 * attempted.
 */
function fakeWidget(options: {
  path: string;
  contentType?: 'notebook' | 'file' | 'directory';
  dirty?: boolean;
}): { widget: IDocumentWidget; revert: jest.Mock } {
  const revert = jest.fn().mockResolvedValue(undefined);
  const contentsModel =
    options.contentType === undefined ? null : { type: options.contentType };
  const widget = {
    context: {
      path: options.path,
      contentsModel,
      model: { dirty: options.dirty ?? false },
      revert
    }
  } as unknown as IDocumentWidget;
  return { widget, revert };
}

/** A minimal registry backed by a Map, exposing only `get`. */
function fakeRegistry(
  entries: Array<[string, IDocumentWidget]>
): ILiveDocumentRegistry {
  const map = new Map(entries);
  return { get: (path: string) => map.get(path) } as ILiveDocumentRegistry;
}

describe('applyServerUpdate', () => {
  it('reverts a clean, non-excluded document', () => {
    const { widget, revert } = fakeWidget({
      path: 'notes.txt',
      contentType: 'file'
    });
    const registry = fakeRegistry([['notes.txt', widget]]);

    applyServerUpdate(registry, 'notes.txt');

    expect(revert).toHaveBeenCalledTimes(1);
  });

  it('does NOT revert a notebook (excluded)', () => {
    const { widget, revert } = fakeWidget({
      path: 'work.ipynb',
      contentType: 'notebook'
    });
    const registry = fakeRegistry([['work.ipynb', widget]]);

    applyServerUpdate(registry, 'work.ipynb');

    expect(revert).not.toHaveBeenCalled();
  });

  it('does NOT revert a notebook even when clean and marked as a plain file type', () => {
    // Guards against contentsModel being unavailable: the .ipynb extension
    // alone must keep the notebook out of the revert path.
    const { widget, revert } = fakeWidget({ path: 'work.ipynb' });
    const registry = fakeRegistry([['work.ipynb', widget]]);

    applyServerUpdate(registry, 'work.ipynb');

    expect(revert).not.toHaveBeenCalled();
  });

  it('does NOT revert a dirty document', () => {
    const { widget, revert } = fakeWidget({
      path: 'notes.txt',
      contentType: 'file',
      dirty: true
    });
    const registry = fakeRegistry([['notes.txt', widget]]);

    applyServerUpdate(registry, 'notes.txt');

    expect(revert).not.toHaveBeenCalled();
  });

  it('is a no-op when the path is not open in this client', () => {
    const { widget, revert } = fakeWidget({
      path: 'notes.txt',
      contentType: 'file'
    });
    const registry = fakeRegistry([['notes.txt', widget]]);

    expect(() => applyServerUpdate(registry, 'other.txt')).not.toThrow();
    expect(revert).not.toHaveBeenCalled();
  });
});
