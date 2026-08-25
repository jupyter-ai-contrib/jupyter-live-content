import { expect, test } from '@jupyterlab/galata';

/**
 * Don't load JupyterLab before the test so we can capture all log messages.
 */
test.use({ autoGoto: false });

const FILE_NAME = 'live-content-e2e.txt';
const INITIAL = 'initial content from disk';
const UPDATED = 'UPDATED content written directly to disk';

test('all three live-content plugins activate', async ({ page }) => {
  const logs: string[] = [];
  page.on('console', message => logs.push(message.text()));

  await page.goto();

  for (const name of ['connector', 'tracker', 'applier']) {
    expect(
      logs.filter(
        s => s === `@jupyter-ai-contrib/live-content:${name} is activated`
      )
    ).toHaveLength(1);
  }
});

test('open document reloads when its file changes on disk', async ({
  page,
  tmpPath
}) => {
  const filePath = `${tmpPath}/${FILE_NAME}`;

  // Seed the file on disk (through the contents API => a real file on the
  // server's filesystem) before opening it.
  await page.contents.uploadContent(INITIAL, 'text', filePath);

  await page.goto();

  // Open the file in the editor.
  await page.filebrowser.open(filePath);

  const editor = page.locator('.jp-FileEditor .cm-content');
  await expect(editor).toContainText(INITIAL);

  // Simulate an out-of-band change: overwrite the file on disk. The document is
  // clean, so the server's watchfiles watcher detects the write, broadcasts a
  // `server_update`, and the applier plugin reloads it via context.revert().
  await page.contents.uploadContent(UPDATED, 'text', filePath);

  await expect(editor).toContainText(UPDATED, { timeout: 15000 });
  await expect(editor).not.toContainText(INITIAL);
});

const NB_NAME = 'live-content-e2e.ipynb';
const NB_ORIGINAL = 'original_cell_source = 1';
const NB_UPDATED = 'updated_cell_source = 2';

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

test('open notebook is NOT reloaded when its file changes on disk', async ({
  page,
  tmpPath
}) => {
  const nbPath = `${tmpPath}/${NB_NAME}`;
  const barrierPath = `${tmpPath}/${FILE_NAME}`;

  // Seed a notebook and a plain text "barrier" file on disk.
  await page.contents.uploadContent(notebook(NB_ORIGINAL), 'text', nbPath);
  await page.contents.uploadContent(INITIAL, 'text', barrierPath);

  await page.goto();

  // Open the notebook, then the text file (so the text editor is the visible
  // tab we can assert on for the barrier below).
  await page.filebrowser.open(nbPath);
  await page.filebrowser.open(barrierPath);

  const editor = page.locator('.jp-FileEditor .cm-content');
  await expect(editor).toContainText(INITIAL);

  // Out-of-band disk changes: overwrite the notebook first, then the barrier
  // text file. The server broadcasts a `server_update` for each.
  await page.contents.uploadContent(notebook(NB_UPDATED), 'text', nbPath);
  await page.contents.uploadContent(UPDATED, 'text', barrierPath);

  // Barrier: the text file DOES reload. Once we observe this, the notebook's
  // (earlier) `server_update` has also been delivered and handled - so if the
  // applier were going to touch the notebook, it would have by now.
  await expect(editor).toContainText(UPDATED, { timeout: 15000 });

  // Invariant: the open notebook must never be clobbered by an on-disk change.
  // Its in-memory model still holds the original cell source, not the version
  // now on disk.
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
