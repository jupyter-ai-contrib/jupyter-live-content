/**
 * Configuration for Playwright using default from @jupyterlab/galata
 */
const baseConfig = require('@jupyterlab/galata/lib/playwright-config');

module.exports = {
  ...baseConfig,
  // Route the suite by environment. The RTC "verify disabled" specs live in
  // ./rtc and run only in the CI legs that install an RTC provider
  // (LIVE_CONTENT_RTC=1); the default suite in ./tests runs without RTC.
  testDir: process.env.LIVE_CONTENT_RTC ? './rtc' : './tests',
  webServer: {
    command: 'jlpm start',
    url: 'http://localhost:8888/lab',
    timeout: 120 * 1000,
    reuseExistingServer: !process.env.CI
  }
};
