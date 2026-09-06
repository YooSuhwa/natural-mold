import { defineConfig } from '@playwright/test'
import path from 'node:path'

import {
  assertIsolatedDatabaseEnvironment,
  buildBackendWebServerCommand,
  buildExactLiveTitleFilter,
  buildFrontendWebServerCommand,
  buildPlaywrightProjectUse,
  E2E_PROJECTS,
  getE2EAuthStatePath,
  getE2ERunPaths,
  getLaneDefaultPorts,
  getPlaywrightExecutionPolicy,
  getPlaywrightArtifactsDirectory,
  LIVE_E2E_SPEC_GLOBS,
  resolveConfiguredE2EProject,
  sanitizePlaywrightEnvironment,
} from './scripts/e2e-lane-contract.mjs'

const selectionOnly = process.env.E2E_SELECTION_ONLY === '1'
const configuredE2eLane = process.env.E2E_LANE
const e2eLane = configuredE2eLane || 'scripted'
if (configuredE2eLane && e2eLane !== 'scripted' && e2eLane !== 'live') {
  throw new Error('E2E_LANE must be scripted or live.')
}
const e2eProject = resolveConfiguredE2EProject(e2eLane, process.env, process.argv.slice(2))
const workerEnvironment = sanitizePlaywrightEnvironment(e2eLane, process.env, e2eProject)
for (const name of Object.keys(process.env)) delete process.env[name]
Object.assign(process.env, workerEnvironment)

const skipBackend = false
const defaultPorts = getLaneDefaultPorts(e2eLane)
const authStatePath = getE2EAuthStatePath(e2eLane, process.env, e2eProject)
const runPaths = getE2ERunPaths(e2eLane, process.env, e2eProject)
const playwrightArtifactsDirectory = getPlaywrightArtifactsDirectory(
  e2eLane,
  process.env,
  e2eProject,
)
const executionPolicy = getPlaywrightExecutionPolicy()
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
process.env.E2E_PROJECT = e2eProject
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${frontendPort}`
const apiBaseURL = process.env.E2E_API_BASE_URL ?? `http://localhost:${backendPort}`
const corsOrigins = `http://localhost:${frontendPort},http://127.0.0.1:${frontendPort}`
const testTimeout = Number(process.env.E2E_TEST_TIMEOUT_MS ?? '60000')
const chatRuntime = process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy' ? 'legacy' : 'langgraph_v3'
process.env.NEXT_PUBLIC_CHAT_RUNTIME = chatRuntime

if (!selectionOnly && !skipBackend) assertIsolatedDatabaseEnvironment(e2eLane, process.env)

if (
  e2eLane === 'live' &&
  (!process.env.E2E_LLM_BASE_URL || !process.env.E2E_LLM_API_KEY || !process.env.E2E_LLM_MODEL)
) {
  throw new Error(
    'The live E2E lane requires E2E_LLM_BASE_URL, E2E_LLM_API_KEY, and E2E_LLM_MODEL.',
  )
}

const runtimeWebServers = [
  ...(skipBackend
    ? []
    : [
        {
          // SKILL_EVALUATION_ENABLED 기본 false(기존 계약 — eval worker 소음 차단).
          // Phase 3 평가 런 투어만 E2E_SKILL_EVALUATION_ENABLED=true로 켠다.
          command: buildBackendWebServerCommand(
            e2eLane,
            process.env.E2E_SKILL_EVALUATION_ENABLED === 'true',
            corsOrigins,
            backendPort,
          ),
          cwd: backendSourceRoot,
          port: backendPort,
          reuseExistingServer: false,
        },
      ]),
  {
    command: buildFrontendWebServerCommand(chatRuntime, apiBaseURL, frontendPort),
    port: frontendPort,
    reuseExistingServer: false,
  },
]

const legacyCaptureSpecGlobs = [
  '**/captures/**/*.spec.ts',
  '**/chat-dictation.spec.ts',
  '**/chat-langgraph-v3-visual-matrix.spec.ts',
]
const runtimePolicyCaptureSpecGlobs = [
  '**/agent-settings.spec.ts',
  '**/runtime-todo-policy.spec.ts',
  '**/runtime-filesystem-policy.spec.ts',
  '**/chat-compaction.spec.ts',
  '**/chat-mcp-apps.spec.ts',
  '**/chat-message-queue.spec.ts',
]
const scriptedFullIgnore = [
  ...LIVE_E2E_SPEC_GLOBS,
  ...legacyCaptureSpecGlobs,
  '**/*live*.spec.ts',
  '**/manual/**',
  '**/manual*.spec.ts',
]
const projects = [
  {
    name: E2E_PROJECTS[0],
    testMatch: '**/smoke.spec.ts',
    use: buildPlaywrightProjectUse(E2E_PROJECTS[0]),
  },
  {
    name: E2E_PROJECTS[1],
    testIgnore: scriptedFullIgnore,
    use: buildPlaywrightProjectUse(E2E_PROJECTS[1]),
  },
  {
    name: E2E_PROJECTS[2],
    testMatch: [...legacyCaptureSpecGlobs, ...runtimePolicyCaptureSpecGlobs],
    use: buildPlaywrightProjectUse(E2E_PROJECTS[2]),
  },
  {
    name: E2E_PROJECTS[3],
    testMatch: [...LIVE_E2E_SPEC_GLOBS],
    grep: new RegExp(buildExactLiveTitleFilter()),
    use: buildPlaywrightProjectUse(E2E_PROJECTS[3]),
  },
]

export default defineConfig({
  testDir: './e2e',
  outputDir: playwrightArtifactsDirectory,
  timeout: testTimeout,
  globalSetup: selectionOnly ? undefined : './e2e/global-setup.mjs',
  retries: executionPolicy.retries,
  workers: executionPolicy.workers,
  reporter: [
    ['json', { outputFile: path.join(runPaths.resultsDir, 'execution.json') }],
    ['list'],
    ['junit', { outputFile: path.join(runPaths.resultsDir, 'junit.xml') }],
  ],
  use: {
    baseURL,
    storageState: authStatePath,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: selectionOnly ? [] : runtimeWebServers,
  projects,
})
