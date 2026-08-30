import { defineConfig } from '@playwright/test'
import path from 'node:path'

import {
  assertIsolatedDatabaseEnvironment,
  getE2EAuthStatePath,
  getE2ERunPaths,
  getLaneDefaultPorts,
  LIVE_E2E_SPECS,
  LIVE_E2E_TEST_MATCH,
} from './scripts/e2e-lane-contract.mjs'

// `PW_SKIP_BACKEND=1` skips spinning up the FastAPI backend. Useful for specs
// that mock every `/api/*` request via `page.route` (M9 health, M10 spend
// dashboard, model fallback) and don't need a live backend. Defaults to off so
// integration specs continue to boot the full stack.
const skipBackend = process.env.PW_SKIP_BACKEND === '1'
const configuredE2eLane = process.env.E2E_LANE
const e2eLane = configuredE2eLane || 'scripted'
const defaultPorts = getLaneDefaultPorts(e2eLane)
const authStatePath = getE2EAuthStatePath(e2eLane, process.env)
const runPaths = getE2ERunPaths(e2eLane, process.env)
const backendSourceRoot = process.env.MOLDY_BACKEND_SOURCE_ROOT ?? path.resolve('../backend')
if (process.env.MOLDY_TEST_RUN_ROOT && !path.isAbsolute(backendSourceRoot)) {
  throw new Error('MOLDY_BACKEND_SOURCE_ROOT must be absolute in isolated mode.')
}
const frontendPort = Number(process.env.E2E_FRONTEND_PORT ?? defaultPorts.frontend)
const backendPort = Number(process.env.E2E_BACKEND_PORT ?? defaultPorts.backend)
process.env.E2E_FRONTEND_PORT = String(frontendPort)
process.env.E2E_BACKEND_PORT = String(backendPort)
process.env.E2E_AUTH_STATE_PATH = authStatePath
process.env.E2E_NEXT_BUILD_DIR = runPaths.buildDir
process.env.E2E_RESULTS_DIR = runPaths.resultsDir
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${frontendPort}`
const apiBaseURL = process.env.E2E_API_BASE_URL ?? `http://localhost:${backendPort}`
const corsOrigins = `http://localhost:${frontendPort},http://127.0.0.1:${frontendPort}`
const workers = Number(process.env.E2E_WORKERS ?? '1')
const testTimeout = Number(process.env.E2E_TEST_TIMEOUT_MS ?? '60000')
const chatRuntime = process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy' ? 'legacy' : 'langgraph_v3'
const reuseExistingServer = process.env.PW_REUSE_EXISTING_SERVER === '1'
process.env.NEXT_PUBLIC_CHAT_RUNTIME = chatRuntime

if (configuredE2eLane && e2eLane !== 'scripted' && e2eLane !== 'live') {
  throw new Error('E2E_LANE must be scripted or live.')
}

if (!skipBackend) assertIsolatedDatabaseEnvironment(e2eLane, process.env)

if (e2eLane === 'live' && (!process.env.E2E_LLM_BASE_URL || !process.env.E2E_LLM_API_KEY || !process.env.E2E_LLM_MODEL)) {
  throw new Error('The live E2E lane requires E2E_LLM_BASE_URL, E2E_LLM_API_KEY, and E2E_LLM_MODEL.')
}

const backendModelEnvironment =
  e2eLane === 'scripted'
    ? 'E2E_LLM_BASE_URL= E2E_LLM_API_KEY= E2E_LLM_MODEL= E2E_SCRIPTED_MODEL_ENABLED=true'
    : 'E2E_SCRIPTED_MODEL_ENABLED=false'

const webServer = [
  ...(skipBackend
    ? []
    : [
        {
          // SKILL_EVALUATION_ENABLED 기본 false(기존 계약 — eval worker 소음 차단).
          // Phase 3 평가 런 투어만 E2E_SKILL_EVALUATION_ENABLED=true로 켠다.
          command: `${backendModelEnvironment} SKILL_EVALUATION_ENABLED=${process.env.E2E_SKILL_EVALUATION_ENABLED === 'true' ? 'true' : 'false'} CORS_ALLOWED_ORIGINS=${corsOrigins} uv run uvicorn app.main:app --port ${backendPort}`,
          cwd: backendSourceRoot,
          port: backendPort,
          reuseExistingServer,
        },
      ]),
  {
    command: `pnpm prepare:assets && NEXT_PUBLIC_CHAT_RUNTIME=${chatRuntime} NEXT_PUBLIC_API_BASE_URL=${apiBaseURL} pnpm exec next dev --port ${frontendPort}`,
    port: frontendPort,
    reuseExistingServer,
  },
]

export default defineConfig({
  testDir: './e2e',
  outputDir: runPaths.resultsDir,
  testMatch: e2eLane === 'live' ? [...LIVE_E2E_TEST_MATCH] : undefined,
  testIgnore: e2eLane === 'scripted' ? [...LIVE_E2E_SPECS] : undefined,
  timeout: testTimeout,
  globalSetup: './e2e/global-setup.mjs',
  retries: 1,
  workers,
  use: {
    baseURL,
    storageState: authStatePath,
    trace: 'on-first-retry',
  },
  webServer,
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
