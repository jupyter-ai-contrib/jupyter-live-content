import { expect, test } from '@jupyterlab/galata';

// Load JupyterLab manually so we can capture activation log messages.
test.use({ autoGoto: false });

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
