/**
 * Configuration for Playwright using default from @jupyterlab/galata.
 *
 * We deliberately do NOT reuse an existing server and do NOT use JupyterLab's
 * default port 8888, so these tests never collide with a JupyterLab you happen
 * to be running locally. The port comes from TEST_PORT (default 8989) and is
 * auto-incremented to the next free port if it is occupied. The chosen port is
 * exported back into the environment so `jlpm start` (jupyter_server_test_config.py)
 * binds the same port.
 */
const baseConfig = require('@jupyterlab/galata/lib/playwright-config');
const { execFileSync } = require('child_process');

function findFreePort(start) {
  const script = `
const net = require('net');
const start = ${start};
function free(port) {
  return new Promise(resolve => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.listen(port, '127.0.0.1', () => srv.close(() => resolve(true)));
  });
}
(async () => {
  for (let p = start; p < start + 200; p++) {
    if (await free(p)) { process.stdout.write(String(p)); return; }
  }
  process.stdout.write(String(start));
})();
`;
  const out = execFileSync(process.execPath, ['-e', script], {
    encoding: 'utf-8'
  });
  return parseInt(out.trim(), 10);
}

// Playwright re-loads this config in each worker process, so resolve the free
// port exactly once (in the first process that loads it) and pin it via a
// sentinel env var that child workers and the webServer inherit. Re-scanning
// per worker would pick a different port once the server occupies the first one.
let port;
if (process.env.LC_RESOLVED_TEST_PORT) {
  port = parseInt(process.env.LC_RESOLVED_TEST_PORT, 10);
} else {
  const requested = parseInt(process.env.TEST_PORT || '8989', 10);
  port = findFreePort(requested);
  process.env.LC_RESOLVED_TEST_PORT = String(port);
  // Propagate to the jupyter server started by `jlpm start`.
  process.env.TEST_PORT = String(port);
}

module.exports = {
  ...baseConfig,
  use: {
    ...baseConfig.use,
    baseURL: `http://localhost:${port}`
  },
  webServer: {
    command: 'jlpm start',
    url: `http://localhost:${port}/lab`,
    timeout: 120 * 1000,
    reuseExistingServer: false
  }
};
