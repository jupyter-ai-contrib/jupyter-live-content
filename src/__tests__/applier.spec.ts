import { IDocumentWidget } from '@jupyterlab/docregistry';
import { FileEditor } from '@jupyterlab/fileeditor';
import { MarkdownViewer } from '@jupyterlab/markdownviewer';

import { applyServerUpdate } from '../applier';
import { ILiveDocumentRegistry } from '../tokens';

// Mock the (ESM-heavy) widget modules; the same mocks back the predicate.
jest.mock('@jupyterlab/fileeditor', () => ({
  FileEditor: class FileEditor {}
}));
jest.mock('@jupyterlab/markdownviewer', () => ({
  MarkdownViewer: class MarkdownViewer {}
}));

/** Passes `instanceof FileEditor` without constructing a real one. */
const fileEditorLike = Object.create(FileEditor.prototype);
/** Passes `instanceof MarkdownViewer`. */
const markdownViewerLike = Object.create(MarkdownViewer.prototype);
/** Stand-in for a non-eligible widget (e.g. a notebook panel). */
const otherWidget = {};

/**
 * Build a fake `IDocumentWidget` exposing what `applyServerUpdate` +
 * `isEligibleForLiveUpdate` read, plus a jest mock for `context.revert`.
 */
function fakeWidget(options: {
  path: string;
  content?: unknown;
  readOnly?: boolean;
  collaborative?: boolean;
  dirty?: boolean;
}): { widget: IDocumentWidget; revert: jest.Mock } {
  const revert = jest.fn().mockResolvedValue(undefined);
  const widget = {
    content: options.content ?? fileEditorLike,
    context: {
      path: options.path,
      revert,
      model: {
        dirty: options.dirty ?? false,
        readOnly: options.readOnly ?? false,
        collaborative: options.collaborative ?? false
      }
    }
  } as unknown as IDocumentWidget;
  return { widget, revert };
}

function fakeRegistry(
  entries: Array<[string, IDocumentWidget]>
): ILiveDocumentRegistry {
  const map = new Map(entries);
  return { get: (path: string) => map.get(path) } as ILiveDocumentRegistry;
}

describe('applyServerUpdate', () => {
  it('reverts a clean, plain file editor', () => {
    const { widget, revert } = fakeWidget({ path: 'notes.txt' });
    applyServerUpdate(fakeRegistry([['notes.txt', widget]]), 'notes.txt');
    expect(revert).toHaveBeenCalledTimes(1);
  });

  it('reverts a read-only viewer', () => {
    const { widget, revert } = fakeWidget({
      path: 'data.csv',
      content: otherWidget,
      readOnly: true
    });
    applyServerUpdate(fakeRegistry([['data.csv', widget]]), 'data.csv');
    expect(revert).toHaveBeenCalledTimes(1);
  });

  it('reverts a markdown preview (MarkdownViewer)', () => {
    const { widget, revert } = fakeWidget({
      path: 'notes.md',
      content: markdownViewerLike
    });
    applyServerUpdate(fakeRegistry([['notes.md', widget]]), 'notes.md');
    expect(revert).toHaveBeenCalledTimes(1);
  });

  it('does NOT revert a notebook / non-file-editor widget', () => {
    const { widget, revert } = fakeWidget({
      path: 'work.ipynb',
      content: otherWidget
    });
    applyServerUpdate(fakeRegistry([['work.ipynb', widget]]), 'work.ipynb');
    expect(revert).not.toHaveBeenCalled();
  });

  it('does NOT revert a collaborative (RTC-backed) document', () => {
    const { widget, revert } = fakeWidget({
      path: 'notes.txt',
      collaborative: true
    });
    applyServerUpdate(fakeRegistry([['notes.txt', widget]]), 'notes.txt');
    expect(revert).not.toHaveBeenCalled();
  });

  it('does NOT revert a dirty file editor', () => {
    const { widget, revert } = fakeWidget({ path: 'notes.txt', dirty: true });
    applyServerUpdate(fakeRegistry([['notes.txt', widget]]), 'notes.txt');
    expect(revert).not.toHaveBeenCalled();
  });

  it('is a no-op when the path is not open in this client', () => {
    const { widget, revert } = fakeWidget({ path: 'notes.txt' });
    const registry = fakeRegistry([['notes.txt', widget]]);
    expect(() => applyServerUpdate(registry, 'other.txt')).not.toThrow();
    expect(revert).not.toHaveBeenCalled();
  });
});
