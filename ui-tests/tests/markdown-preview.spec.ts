import { expect, test } from '@jupyterlab/galata';

test.use({ autoGoto: false });

const MD_NAME = 'live-content-e2e.md';
const INITIAL = 'InitialMarkdownContent';
const UPDATED = 'UpdatedMarkdownContent';

test('markdown preview re-renders when its file changes on disk', async ({
  page,
  tmpPath
}) => {
  const mdPath = `${tmpPath}/${MD_NAME}`;

  await page.contents.uploadContent(`# ${INITIAL}`, 'text', mdPath);

  await page.goto();

  // Bring up the rendered Markdown Preview (Open With -> Markdown Preview).
  await page.evaluate(
    p =>
      (window as any).jupyterapp.commands.execute('docmanager:open', {
        path: p,
        factory: 'Markdown Preview'
      }),
    mdPath
  );

  const rendered = page.locator('.jp-RenderedMarkdown');
  await expect(rendered).toContainText(INITIAL);

  // Out-of-band change: overwrite the markdown on disk. The preview is a
  // MarkdownViewer (allowlisted), so the applier reverts its context and the
  // view re-renders from the new content.
  await page.contents.uploadContent(`# ${UPDATED}`, 'text', mdPath);

  await expect(rendered).toContainText(UPDATED, { timeout: 15000 });
  await expect(rendered).not.toContainText(INITIAL);
});
