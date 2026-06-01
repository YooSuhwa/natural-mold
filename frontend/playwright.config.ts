import { defineConfig } from '@playwright/test'

// `PW_SKIP_BACKEND=1` skips spinning up the FastAPI backend. Useful for specs
// that mock every `/api/*` request via `page.route` (M9 health, M10 spend
// dashboard, model fallback) and don't need a live backend. Defaults to off so
// integration specs continue to boot the full stack.
const skipBackend = process.env.PW_SKIP_BACKEND === '1'
const frontendPort = Number(process.env.E2E_FRONTEND_PORT ?? '3000')
const backendPort = Number(process.env.E2E_BACKEND_PORT ?? '8001')
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${frontendPort}`
const apiBaseURL = process.env.E2E_API_BASE_URL ?? `http://localhost:${backendPort}`
const corsOrigins = `http://localhost:${frontendPort},http://127.0.0.1:${frontendPort}`
const workers = Number(process.env.E2E_WORKERS ?? '4')
const testTimeout = Number(process.env.E2E_TEST_TIMEOUT_MS ?? '60000')

const webServer = [
  ...(skipBackend
    ? []
    : [
        {
          command: `cd ../backend && CORS_ALLOWED_ORIGINS=${corsOrigins} uv run uvicorn app.main:app --port ${backendPort}`,
          port: backendPort,
          reuseExistingServer: true,
        },
      ]),
  {
    command: `NEXT_PUBLIC_API_BASE_URL=${apiBaseURL} pnpm dev --port ${frontendPort}`,
    port: frontendPort,
    reuseExistingServer: true,
  },
]

export default defineConfig({
  testDir: './e2e',
  timeout: testTimeout,
  globalSetup: './e2e/global-setup.mjs',
  retries: 1,
  workers,
  use: {
    baseURL,
    storageState: './e2e/.auth/user.json',
    trace: 'on-first-retry',
  },
  webServer,
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
