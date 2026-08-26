import { expect, test } from '@jupyterlab/galata';

/**
 * Isolated E2E suite that only runs when an RTC provider is installed and
 * enabled (see the `LIVE_CONTENT_RTC` legs in `.github/workflows/build.yml`).
 *
 * When RTC is active, `jupyterlab-live-content` must stand completely down:
 * the server extension registers no WebSocket handler and starts no file
 * watcher, and the frontend opens no connection. These tests assert that from
 * a real browser against a real RTC server.
 */

// Load JupyterLab manually so we can observe WebSockets from the first frame.
test.use({ autoGoto: false });

const WS_PATH = 'jupyterlab-live-content/ws';

function pageConfig(page: import('@playwright/test').Page): Promise<any> {
  return page.evaluate(() => {
    const el = document.getElementById('jupyter-config-data');
    return el && el.textContent ? JSON.parse(el.textContent) : {};
  });
}

test('server advertises that live-content is disabled', async ({ page }) => {
  await page.goto();
  const cfg = await pageConfig(page);
  expect(cfg.liveContentServerDisabled).toBe(true);
});

test('the live-content WebSocket endpoint is not served', async ({ page }) => {
  await page.goto();

  // A GET to an unregistered route returns 404. If the handler WERE registered
  // it would instead reject the non-upgrade request (400/403) - never 404.
  const status = await page.evaluate(async (wsPath: string) => {
    const el = document.getElementById('jupyter-config-data');
    const baseUrl =
      (el && el.textContent && JSON.parse(el.textContent).baseUrl) || '/';
    const res = await fetch(baseUrl + wsPath);
    return res.status;
  }, WS_PATH);

  expect(status).toBe(404);
});

test('the frontend opens no live-content WebSocket', async ({ page }) => {
  const liveContentSockets: string[] = [];
  page.on('websocket', ws => {
    if (ws.url().includes(WS_PATH)) {
      liveContentSockets.push(ws.url());
    }
  });

  await page.goto();
  // Give the connector plugin ample time to (not) connect. The RTC provider's
  // own sockets are ignored by the filter above.
  await page.waitForTimeout(3000);

  expect(liveContentSockets).toEqual([]);
});
