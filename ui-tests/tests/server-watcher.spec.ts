import { expect, test } from '@jupyterlab/galata';
import type { Page } from '@playwright/test';

test.use({ autoGoto: false });

/**
 * Read the directories the server is currently watching (relative to the server
 * root), via the E2E-only test endpoint registered in
 * `ui-tests/_live_content_test_ext.py`.
 */
function watchedDirs(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    const el = document.getElementById('jupyter-config-data');
    const baseUrl =
      (el && el.textContent && JSON.parse(el.textContent).baseUrl) || '/';
    const res = await fetch(baseUrl + 'api/live-content/_test/watched');
    const body = await res.json();
    return (body.watched ?? []) as string[];
  });
}

/**
 * The watched directories under `prefix`. The test server is shared across all
 * spec files, so we scope assertions to this test's own tmp directory rather
 * than asserting on the global watch set.
 */
async function watchedUnder(page: Page, prefix: string): Promise<string[]> {
  const all = await watchedDirs(page);
  return all.filter(d => d === prefix || d.startsWith(prefix + '/')).sort();
}

/** Close the open document at `path` (fires client_closed to the server). */
function closeDocument(page: Page, path: string): Promise<void> {
  return page.evaluate((p: string) => {
    const app = (window as any).jupyterapp;
    for (const widget of app.shell.widgets('main')) {
      const context = (widget as any).context;
      if (context && context.path === p) {
        (widget as any).close();
        return;
      }
    }
  }, path);
}

async function seedNested(page: Page, tmpPath: string) {
  const l1 = `${tmpPath}/level1`;
  const l2 = `${l1}/level2`;
  const l3 = `${l2}/level3`;
  await page.contents.createDirectory(l1);
  await page.contents.createDirectory(l2);
  await page.contents.createDirectory(l3);
  const f1 = `${l1}/untitled.txt`;
  const f2 = `${l2}/untitled.txt`;
  const f3 = `${l3}/untitled.txt`;
  for (const f of [f1, f2, f3]) {
    await page.contents.uploadContent('content', 'text', f);
  }
  return { l1, l2, l3, f1, f2, f3 };
}

test('the watch set follows the documents open in nested directories', async ({
  page,
  tmpPath
}) => {
  const { l1, l2, l3, f1, f2, f3 } = await seedNested(page, tmpPath);

  await page.goto();

  // Nothing open yet -> nothing watched under this test's directory.
  await expect.poll(() => watchedUnder(page, tmpPath)).toEqual([]);

  // Opening each nested document adds its directory to the watch set.
  await page.filebrowser.open(f1);
  await expect.poll(() => watchedUnder(page, tmpPath)).toEqual([l1]);

  await page.filebrowser.open(f2);
  await expect.poll(() => watchedUnder(page, tmpPath)).toEqual([l1, l2]);

  await page.filebrowser.open(f3);
  await expect.poll(() => watchedUnder(page, tmpPath)).toEqual([l1, l2, l3]);

  // Closing each document removes its directory again (deepest first).
  await closeDocument(page, f3);
  await expect.poll(() => watchedUnder(page, tmpPath)).toEqual([l1, l2]);

  await closeDocument(page, f2);
  await expect.poll(() => watchedUnder(page, tmpPath)).toEqual([l1]);

  await closeDocument(page, f1);
  await expect.poll(() => watchedUnder(page, tmpPath)).toEqual([]);
});

test("closing the tab stops watching all of that client's files", async ({
  page,
  tmpPath
}) => {
  const { l1, l2, l3, f1, f2, f3 } = await seedNested(page, tmpPath);

  await page.goto();
  await page.filebrowser.open(f1);
  await page.filebrowser.open(f2);
  await page.filebrowser.open(f3);
  await expect.poll(() => watchedUnder(page, tmpPath)).toEqual([l1, l2, l3]);

  // Probe the server over HTTP using the browser context's request API, which
  // shares the page's auth cookies but has no page of its own - so it survives
  // the page (tab) being closed below.
  const probeUrl = await page.evaluate(() => {
    const el = document.getElementById('jupyter-config-data');
    const cfg = el && el.textContent ? JSON.parse(el.textContent) : {};
    let url =
      location.origin + (cfg.baseUrl || '/') + 'api/live-content/_test/watched';
    if (cfg.token) {
      url += '?token=' + encodeURIComponent(cfg.token);
    }
    return url;
  });
  const context = page.context();
  const probeWatchedUnder = async (): Promise<string[]> => {
    const res = await context.request.get(probeUrl);
    const body = await res.json();
    return ((body.watched ?? []) as string[])
      .filter(d => d === tmpPath || d.startsWith(tmpPath + '/'))
      .sort();
  };

  // Sanity: the request-API probe sees the same open documents.
  await expect.poll(probeWatchedUnder).toEqual([l1, l2, l3]);

  // The user closes the tab: the WebSocket drops and the server must stop
  // watching everything that client had open.
  await page.close();

  await expect.poll(probeWatchedUnder).toEqual([]);
});

test('the watch set persists until the last client with the files open closes', async ({
  page,
  browser,
  request,
  tmpPath
}) => {
  const { l1, l2, l3, f1, f2, f3 } = await seedNested(page, tmpPath);

  // Client A: the galata page.
  await page.goto();
  await page.filebrowser.open(f1);
  await page.filebrowser.open(f2);
  await page.filebrowser.open(f3);

  // An authenticated probe URL and a raw lab URL for the second client.
  const { probeUrl, labUrl } = await page.evaluate(() => {
    const el = document.getElementById('jupyter-config-data');
    const cfg = el && el.textContent ? JSON.parse(el.textContent) : {};
    const base = cfg.baseUrl || '/';
    const q = cfg.token ? '?token=' + encodeURIComponent(cfg.token) : '';
    return {
      probeUrl: location.origin + base + 'api/live-content/_test/watched' + q,
      labUrl: location.origin + base + 'lab' + q
    };
  });

  const probeWatchedUnder = async (): Promise<string[]> => {
    const res = await request.get(probeUrl);
    const body = await res.json();
    return ((body.watched ?? []) as string[])
      .filter(d => d === tmpPath || d.startsWith(tmpPath + '/'))
      .sort();
  };

  // Client B: a second, independent web client (separate context => separate
  // WebSocket) opening the same three documents.
  const clientB = await browser.newContext();
  const pageB = await clientB.newPage();
  await pageB.goto(labUrl);
  await pageB.waitForFunction(() =>
    (window as any).jupyterapp?.commands?.hasCommand('docmanager:open')
  );
  await pageB.evaluate(
    async (paths: string[]) => {
      const app = (window as any).jupyterapp;
      for (const p of paths) {
        await app.commands.execute('docmanager:open', { path: p });
      }
    },
    [f1, f2, f3]
  );

  // Both clients have all three documents open.
  await expect.poll(probeWatchedUnder).toEqual([l1, l2, l3]);

  // Client B disconnects. Client A still has them open, so the watch set must
  // stay exactly the same.
  await clientB.close();
  await expect.poll(probeWatchedUnder).toEqual([l1, l2, l3]);
  // Give the server time to process B's disconnect and confirm no drift.
  await page.waitForTimeout(1500);
  expect(await probeWatchedUnder()).toEqual([l1, l2, l3]);

  // Client A also disconnects: now nothing is watched.
  await page.close();
  await expect.poll(probeWatchedUnder).toEqual([]);
});
