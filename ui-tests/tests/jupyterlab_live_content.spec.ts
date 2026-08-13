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
