import { IDocumentWidget } from '@jupyterlab/docregistry';
import { FileEditor } from '@jupyterlab/fileeditor';
import { MarkdownViewer } from '@jupyterlab/markdownviewer';

import { isEligibleForLiveUpdate } from '../eligibility';

// Mock the (ESM-heavy) widget modules: we only need classes to test the
// `instanceof` branches. The same mocks back the predicate's imports.
jest.mock('@jupyterlab/fileeditor', () => ({
  FileEditor: class FileEditor {}
}));
jest.mock('@jupyterlab/markdownviewer', () => ({
  MarkdownViewer: class MarkdownViewer {}
}));

/**
 * Build a minimal `IDocumentWidget` exposing only what the predicate reads:
 * `content` (for the `instanceof` checks) and
 * `context.model.{readOnly,collaborative}`.
 */
function fakeWidget(options: {
  content: unknown;
  readOnly?: boolean;
  collaborative?: boolean;
}): IDocumentWidget {
  return {
    content: options.content,
    context: {
      model: {
        readOnly: options.readOnly ?? false,
        collaborative: options.collaborative ?? false
      }
    }
  } as unknown as IDocumentWidget;
}

const fileEditorLike = Object.create(FileEditor.prototype);
const markdownViewerLike = Object.create(MarkdownViewer.prototype);
/** A stand-in for any non-eligible content (e.g. a notebook panel). */
const otherWidget = {};

describe('isEligibleForLiveUpdate', () => {
  it('allows a plain (non-collaborative) file editor', () => {
    expect(
      isEligibleForLiveUpdate(fakeWidget({ content: fileEditorLike }))
    ).toBe(true);
  });

  it('allows a markdown preview (MarkdownViewer)', () => {
    expect(
      isEligibleForLiveUpdate(fakeWidget({ content: markdownViewerLike }))
    ).toBe(true);
  });

  it('allows a read-only document regardless of its widget type', () => {
    expect(
      isEligibleForLiveUpdate(
        fakeWidget({ content: otherWidget, readOnly: true })
      )
    ).toBe(true);
  });

  it('excludes a collaborative file editor (RTC-backed)', () => {
    expect(
      isEligibleForLiveUpdate(
        fakeWidget({ content: fileEditorLike, collaborative: true })
      )
    ).toBe(false);
  });

  it('excludes a collaborative markdown preview', () => {
    expect(
      isEligibleForLiveUpdate(
        fakeWidget({ content: markdownViewerLike, collaborative: true })
      )
    ).toBe(false);
  });

  it('excludes a non-file-editor document (e.g. a notebook panel)', () => {
    expect(isEligibleForLiveUpdate(fakeWidget({ content: otherWidget }))).toBe(
      false
    );
  });
});
