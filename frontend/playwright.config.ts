import { defineConfig } from '@playwright/test'

// `PW_SKIP_BACKEND=1` skips spinning up the FastAPI backend. Useful for specs
// that mock every `/api/*` request via `page.route` (M9 health, M10 spend
// dashboard, model fallback) and don't need a live backend. Defaults to off so
// integration specs continue to boot the full stack.
const skipBackend = process.env.PW_SKIP_BACKEND === '1'

const webServer = [
  ...(skipBackend
    ? []
    : [
        {
          command: 'cd ../backend && uv run uvicorn app.main:app --port 8001',
          port: 8001,
          reuseExistingServer: true,
        },
      ]),
  {
    command: 'pnpm dev',
    port: 3000,
    reuseExistingServer: true,
  },
]

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 1,
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  webServer,
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
