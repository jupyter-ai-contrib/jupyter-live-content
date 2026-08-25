import { IDocumentWidget } from '@jupyterlab/docregistry';

import { isExcludedFromLiveUpdates } from '../exclusions';

/**
 * Build a minimal `IDocumentWidget` stub carrying only the fields the
 * exclusion predicate reads: `context.path` and `context.contentsModel.type`.
 */
function fakeWidget(
  path: string,
  contentType?: 'notebook' | 'file' | 'directory'
): IDocumentWidget {
  const contentsModel =
    contentType === undefined ? null : { type: contentType };
  return {
    context: {
      path,
      contentsModel
    }
  } as unknown as IDocumentWidget;
}

describe('isExcludedFromLiveUpdates', () => {
  it('excludes notebooks identified by the Contents API type', () => {
    expect(
      isExcludedFromLiveUpdates(fakeWidget('work.ipynb', 'notebook'))
    ).toBe(true);
  });

  it('excludes notebooks by .ipynb extension even when contentsModel is null', () => {
    // `contentsModel` is null before a document finishes loading; we must still
    // recognize notebooks so we never revert them.
    expect(isExcludedFromLiveUpdates(fakeWidget('work.ipynb'))).toBe(true);
  });

  it('excludes notebooks with an uppercase .IPYNB extension', () => {
    expect(isExcludedFromLiveUpdates(fakeWidget('WORK.IPYNB'))).toBe(true);
  });

  it('does not exclude plain text files', () => {
    expect(isExcludedFromLiveUpdates(fakeWidget('notes.txt', 'file'))).toBe(
      false
    );
  });

  it('does not exclude a file whose name merely contains "ipynb"', () => {
    expect(
      isExcludedFromLiveUpdates(fakeWidget('ipynb-notes.txt', 'file'))
    ).toBe(false);
  });

  it('does not exclude a file with a null contentsModel and non-notebook path', () => {
    expect(isExcludedFromLiveUpdates(fakeWidget('data.csv'))).toBe(false);
  });
});
