import { expect, test } from '@jupyterlab/galata';

test.use({ autoGoto: false });

const FILE_NAME = 'live-content-e2e.txt';
const INITIAL = 'initial content from disk';
const UPDATED = 'UPDATED content written directly to disk';

test('open file editor reloads when its file changes on disk', async ({
  page,
  tmpPath
}) => {
  const filePath = `${tmpPath}/${FILE_NAME}`;

  // Seed the file on disk (through the contents API => a real file on the
  // server's filesystem) before opening it.
  await page.contents.uploadContent(INITIAL, 'text', filePath);

  await page.goto();

  // Open the file in the (default) editor.
  await page.filebrowser.open(filePath);

  const editor = page.locator('.jp-FileEditor .cm-content');
  await expect(editor).toContainText(INITIAL);

  // Out-of-band change: overwrite the file on disk. The document is clean, so
  // the server's watcher broadcasts a `server_update` and the applier reloads
  // it via context.revert().
  await page.contents.uploadContent(UPDATED, 'text', filePath);

  await expect(editor).toContainText(UPDATED, { timeout: 15000 });
  await expect(editor).not.toContainText(INITIAL);
});

const SAVE_FILE = 'save-no-revert.txt';

test('saving local edits does not reload the document (no self-revert)', async ({
  page,
  tmpPath
}) => {
  const filePath = `${tmpPath}/${SAVE_FILE}`;
  await page.contents.uploadContent('initial', 'text', filePath);

  await page.goto();
  await page.filebrowser.open(filePath);

  const editor = page.locator('.jp-FileEditor .cm-content');
  await expect(editor).toContainText('initial');

  // Instrument this document's context.revert to count reloads.
  await page.evaluate((p: string) => {
    const app = (window as any).jupyterapp;
    (window as any).__revertCount = 0;
    for (const widget of app.shell.widgets('main')) {
      const context = (widget as any).context;
      if (context && context.path === p) {
        const original = context.revert.bind(context);
        context.revert = (...args: any[]) => {
          (window as any).__revertCount++;
          return original(...args);
        };
      }
    }
  }, filePath);

  // Make a local edit and save it. Saving is our own write, not an out-of-band
  // change, so it must not trigger a reload.
  await editor.click();
  await page.keyboard.type(' EDITED');
  await page.evaluate(() =>
    (window as any).jupyterapp.commands.execute('docmanager:save')
  );

  // Wait past the filesystem watcher debounce so any (unwanted) reload triggered
  // by our own save would have fired by now.
  await page.waitForTimeout(4000);

  const revertCount = await page.evaluate(
    () => (window as any).__revertCount as number
  );
  expect(revertCount).toBe(0);
});
