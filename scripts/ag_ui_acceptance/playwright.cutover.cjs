// The Python runner owns both frontends, both workers, PostgreSQL, and authentication.
const path = require('node:path')
const { createRequire } = require('node:module')

const frontend = process.cwd()
const resolve = createRequire(path.join(frontend, 'package.json')).resolve
const { defineConfig } = require(resolve('@playwright/test'))

module.exports = defineConfig({
  testDir: path.join(frontend, 'e2e'),
  globalSetup: path.join(frontend, 'e2e/global-setup.mjs'),
  use: {
    baseURL: process.env.E2E_BASE_URL,
    storageState: process.env.E2E_AUTH_STATE_PATH,
    browserName: 'chromium',
    locale: 'ko-KR',
    timezoneId: 'UTC',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
  workers: 1,
  retries: 0,
  fullyParallel: false,
  timeout: 180000,
  expect: { timeout: 15000 },
  // Playwright clears its artifact directory before global setup validates the run paths.
  outputDir: path.join(frontend, 'playwright-artifacts', process.env.E2E_PROJECT),
  reporter: [['json', { outputFile: process.env.MOLDY_AGUI_PLAYWRIGHT_JSON }]],
})
