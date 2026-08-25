import { IDocumentWidget } from '@jupyterlab/docregistry';
import { FileEditor } from '@jupyterlab/fileeditor';

import { isEligibleForLiveUpdate } from '../eligibility';

// Mock the (ESM-heavy) file editor module: we only need a class to test the
// `instanceof FileEditor` branch. The same mock backs the predicate's import.
jest.mock('@jupyterlab/fileeditor', () => ({
  FileEditor: class FileEditor {}
}));

/**
 * Build a minimal `IDocumentWidget` exposing only what the predicate reads:
 * `content` (to test the `instanceof FileEditor` check) and
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

/** A stand-in that passes `instanceof FileEditor` without constructing one. */
const fileEditorLike = Object.create(FileEditor.prototype);
/** A stand-in for any non-file-editor content (e.g. a notebook panel). */
const otherWidget = {};

describe('isEligibleForLiveUpdate', () => {
  it('allows a plain (non-collaborative) file editor', () => {
    expect(
      isEligibleForLiveUpdate(fakeWidget({ content: fileEditorLike }))
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

  it('excludes a non-file-editor document (e.g. a notebook)', () => {
    expect(isEligibleForLiveUpdate(fakeWidget({ content: otherWidget }))).toBe(
      false
    );
  });

  it('excludes a non-file-editor, non-read-only, non-collaborative document', () => {
    // e.g. a custom editable widget we don't understand: safe default is skip.
    expect(isEligibleForLiveUpdate(fakeWidget({ content: otherWidget }))).toBe(
      false
    );
  });
});
