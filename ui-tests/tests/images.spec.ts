import { expect, test } from '@jupyterlab/galata';

test.use({ autoGoto: false });

const IMG_NAME = 'live-content-e2e.png';
// Two solid PNGs of different sizes: 3x3 (initial) and 6x6 (updated). Reading
// the rendered <img>'s naturalWidth lets us assert the view actually reloaded.
const IMG_3x3 =
  'iVBORw0KGgoAAAANSUhEUgAAAAMAAAADCAIAAADZSiLoAAAAEElEQVR4nGP4z8AAQQxYWACPjgj4kWPEuQAAAABJRU5ErkJggg==';
const IMG_6x6 =
  'iVBORw0KGgoAAAANSUhEUgAAAAYAAAAGCAIAAABvrngfAAAAEElEQVR4nGNgYPiPgWgqBADY+yPdhYFXrAAAAABJRU5ErkJggg==';

/** Read the naturalWidth of the image rendered for `path`, or null. */
function renderedWidth(
  page: import('@playwright/test').Page,
  path: string
): Promise<number | null> {
  return page.evaluate((p: string) => {
    const app = (window as any).jupyterapp;
    for (const widget of app.shell.widgets('main')) {
      const context = (widget as any).context;
      if (context && context.path === p) {
        const img = (widget as any).node.querySelector('img');
        return img ? img.naturalWidth : null;
      }
    }
    return null;
  }, path);
}

test('image viewer re-renders when its file changes on disk', async ({
  page,
  tmpPath
}) => {
  const imgPath = `${tmpPath}/${IMG_NAME}`;

  await page.contents.uploadContent(IMG_3x3, 'base64', imgPath);

  await page.goto();

  // Image Viewer is the default widget for a .png.
  await page.filebrowser.open(imgPath);
  await page.locator('.jp-ImageViewer img').first().waitFor();

  await expect.poll(() => renderedWidth(page, imgPath)).toBe(3);

  // Out-of-band change: overwrite with a larger image. The ImageViewer is
  // allowlisted, so the applier reverts its context and the view re-renders.
  await page.contents.uploadContent(IMG_6x6, 'base64', imgPath);

  await expect
    .poll(() => renderedWidth(page, imgPath), { timeout: 15000 })
    .toBe(6);
});
