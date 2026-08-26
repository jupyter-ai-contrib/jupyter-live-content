import { expect, test } from '@jupyterlab/galata';

test.use({ autoGoto: false });

const NB_NAME = 'live-content-e2e.ipynb';
const NB_ORIGINAL = 'original_cell_source = 1';
const NB_UPDATED = 'updated_cell_source = 2';

const BARRIER_NAME = 'live-content-barrier.txt';
const BARRIER_INITIAL = 'barrier initial';
const BARRIER_UPDATED = 'barrier UPDATED';

/** A minimal nbformat v4 notebook with a single code cell. */
function notebook(cellSource: string): string {
  return JSON.stringify({
    cells: [
      {
        cell_type: 'code',
        source: cellSource,
        metadata: {},
        outputs: [],
        execution_count: null
      }
    ],
    metadata: {
      kernelspec: {
        name: 'python3',
        display_name: 'Python 3',
        language: 'python'
      }
    },
    nbformat: 4,
    nbformat_minor: 5
  });
}

test('the NotebookPanel view is NOT reloaded when its file changes on disk', async ({
  page,
  tmpPath
}) => {
  const nbPath = `${tmpPath}/${NB_NAME}`;
  const barrierPath = `${tmpPath}/${BARRIER_NAME}`;

  // Seed a notebook and a plain text "barrier" file on disk.
  await page.contents.uploadContent(notebook(NB_ORIGINAL), 'text', nbPath);
  await page.contents.uploadContent(BARRIER_INITIAL, 'text', barrierPath);

  await page.goto();

  // Open the notebook (default NotebookPanel view), then the text file (so the
  // text editor is the visible tab we can assert on for the barrier below).
  await page.filebrowser.open(nbPath);
  await page.filebrowser.open(barrierPath);

  const editor = page.locator('.jp-FileEditor .cm-content');
  await expect(editor).toContainText(BARRIER_INITIAL);

  // Out-of-band disk changes: overwrite the notebook first, then the barrier
  // text file. The server broadcasts a `server_update` for each.
  await page.contents.uploadContent(notebook(NB_UPDATED), 'text', nbPath);
  await page.contents.uploadContent(BARRIER_UPDATED, 'text', barrierPath);

  // Barrier: the text file DOES reload. Once we observe this, the notebook's
  // (earlier) `server_update` has also been delivered and handled - so if the
  // applier were going to touch the notebook, it would have by now.
  await expect(editor).toContainText(BARRIER_UPDATED, { timeout: 15000 });

  // Invariant: the open notebook must never be clobbered by an on-disk change.
  // Its in-memory model still holds the original cell source.
  const cellSource = await page.evaluate((path: string) => {
    const app = (window as any).jupyterapp;
    for (const widget of app.shell.widgets('main')) {
      const context = (widget as any).context;
      if (context && context.path === path) {
        return context.model.cells.get(0).sharedModel.getSource();
      }
    }
    return null;
  }, nbPath);

  expect(cellSource).toContain(NB_ORIGINAL);
  expect(cellSource).not.toContain(NB_UPDATED);
});

test('a notebook opened in the editor view DOES reload live', async ({
  page,
  tmpPath
}) => {
  const nbPath = `${tmpPath}/${NB_NAME}`;

  await page.contents.uploadContent(notebook(NB_ORIGINAL), 'text', nbPath);

  await page.goto();

  // "Open With -> Editor": a FileEditor over the raw .ipynb, which is eligible
  // for live updates (unlike the NotebookPanel view above).
  await page.evaluate(
    p =>
      (window as any).jupyterapp.commands.execute('docmanager:open', {
        path: p,
        factory: 'Editor'
      }),
    nbPath
  );

  const editor = page.locator('.jp-FileEditor .cm-content');
  await expect(editor).toContainText(NB_ORIGINAL);

  await page.contents.uploadContent(notebook(NB_UPDATED), 'text', nbPath);

  await expect(editor).toContainText(NB_UPDATED, { timeout: 15000 });
  await expect(editor).not.toContainText(NB_ORIGINAL);
});
