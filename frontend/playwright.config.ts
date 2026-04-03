import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 1,
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  webServer: [
    {
      command: 'cd ../backend && uv run uvicorn app.main:app --port 8001',
      port: 8001,
      reuseExistingServer: true,
    },
    {
      command: 'pnpm dev',
      port: 3000,
      reuseExistingServer: true,
    },
  ],
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
