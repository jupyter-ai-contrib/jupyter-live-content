import { IDocumentWidget } from '@jupyterlab/docregistry';
import { FileEditor } from '@jupyterlab/fileeditor';
import { ImageViewer } from '@jupyterlab/imageviewer';
import { MarkdownViewer } from '@jupyterlab/markdownviewer';

import { isEligibleForLiveUpdate } from '../eligibility';

// Mock the (ESM-heavy) widget modules: we only need classes to test the
// `instanceof` branches. The same mocks back the predicate's imports.
jest.mock('@jupyterlab/fileeditor', () => ({
  FileEditor: class FileEditor {}
}));
jest.mock('@jupyterlab/imageviewer', () => ({
  ImageViewer: class ImageViewer {}
}));
jest.mock('@jupyterlab/markdownviewer', () => ({
  MarkdownViewer: class MarkdownViewer {}
}));

/**
 * Build a minimal `IDocumentWidget` exposing only what the predicate reads:
 * `content` (for the `instanceof` checks) and `context.model.collaborative`.
 */
function fakeWidget(options: {
  content: unknown;
  collaborative?: boolean;
  readOnly?: boolean;
}): IDocumentWidget {
  return {
    content: options.content,
    context: {
      model: {
        collaborative: options.collaborative ?? false,
        readOnly: options.readOnly ?? false
      }
    }
  } as unknown as IDocumentWidget;
}

const fileEditorLike = Object.create(FileEditor.prototype);
const markdownViewerLike = Object.create(MarkdownViewer.prototype);
const imageViewerLike = Object.create(ImageViewer.prototype);
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

  it('allows an image viewer (ImageViewer)', () => {
    expect(
      isEligibleForLiveUpdate(fakeWidget({ content: imageViewerLike }))
    ).toBe(true);
  });

  it('excludes a collaborative (RTC-backed) file editor', () => {
    expect(
      isEligibleForLiveUpdate(
        fakeWidget({ content: fileEditorLike, collaborative: true })
      )
    ).toBe(false);
  });

  it('excludes a non-allowlisted document (e.g. a notebook panel)', () => {
    expect(isEligibleForLiveUpdate(fakeWidget({ content: otherWidget }))).toBe(
      false
    );
  });

  it('excludes a read-only notebook (read-only blocks saving, not running cells)', () => {
    // A read-only .ipynb is still a NotebookPanel, not an allowlisted viewer.
    expect(
      isEligibleForLiveUpdate(
        fakeWidget({ content: otherWidget, readOnly: true })
      )
    ).toBe(false);
  });
});
