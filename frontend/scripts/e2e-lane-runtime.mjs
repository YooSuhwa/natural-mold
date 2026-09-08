import path from 'node:path'

export const REQUIRED_LIVE_LLM_VARIABLES = Object.freeze([
  'E2E_LLM_BASE_URL',
  'E2E_LLM_API_KEY',
  'E2E_LLM_MODEL',
])

const LANE_PROJECTS = Object.freeze({
  scripted: Object.freeze(['scripted-smoke', 'scripted-full', 'scripted-capture']),
  live: Object.freeze(['live-manual']),
})

const DEFAULT_PROJECTS = Object.freeze({ scripted: 'scripted-full', live: 'live-manual' })

const SAFE_ENVIRONMENT_NAMES = Object.freeze(
  'CI DATABASE_URL DATABASE_URL_SYNC E2E_API_BASE_URL E2E_AUTH_STATE_PATH E2E_BACKEND_PORT E2E_BASE_URL E2E_CAPTURE_TOUR E2E_EXPORT_SLUG E2E_FRONTEND_PORT E2E_NEXT_BUILD_DIR E2E_PROJECT E2E_RESULTS_DIR E2E_RUN_MANIFEST E2E_SELECTION_ONLY E2E_SEED_USER_ENABLED E2E_SKILL_EVALUATION_ENABLED E2E_TEST_HELPERS_ENABLED E2E_TEST_TIMEOUT_MS E2E_USER_EMAIL E2E_USER_NAME E2E_USER_PASSWORD E2E_EMAIL E2E_NAME E2E_PASSWORD ENCRYPTION_KEYS HOME INTEGRATION_DATABASE_URL JWT_SECRET LANG LC_ALL LOGNAME MOLDY_BACKEND_SOURCE_ROOT MOLDY_DISABLE_ENV_FILE MOLDY_FRONTEND_SOURCE_ROOT MOLDY_GATE_PYTHON MOLDY_TEST_RUN_ROOT NEXT_PUBLIC_CHAT_RUNTIME PATH PLAYWRIGHT_JUNIT_OUTPUT_NAME PNPM_HOME PYTHON_DOTENV_DISABLED RATE_LIMIT_ENABLED CHECKPOINTER_POOL_MIN_SIZE CHECKPOINTER_POOL_MAX_SIZE TEMP TMP TMPDIR TZ USER'.split(
    ' ',
  ),
)

const LAUNCHER_ENVIRONMENT_NAMES = Object.freeze(
  'CI E2E_CAPTURE_TOUR E2E_EXPORT_SLUG E2E_RUN_MANIFEST E2E_SKILL_EVALUATION_ENABLED E2E_TEST_TIMEOUT_MS HOME LANG LC_ALL LOGNAME MOLDY_GATE_DOCKER MOLDY_GATE_DOCKER_IDENTITY PATH PNPM_HOME TEMP TMP TMPDIR TZ USER XDG_RUNTIME_DIR'.split(
    ' ',
  ),
)

export const E2E_PROJECTS = Object.freeze([
  'scripted-smoke',
  'scripted-full',
  'scripted-capture',
  'live-manual',
])

export const LIVE_E2E_CASES = Object.freeze([
  Object.freeze({
    spec: 'e2e/builder.spec.ts',
    title: 'starts a session and runs the build pipeline from an initial message',
  }),
  Object.freeze({
    spec: 'e2e/operator-screens.spec.ts',
    title: 'System LLM shows the seed-configured role slots',
  }),
  Object.freeze({
    spec: 'e2e/operator-screens.spec.ts',
    title: 'creates and deletes a system credential through the catalog modal',
  }),
  Object.freeze({
    spec: 'e2e/agent-triggers.spec.ts',
    title: 'a created interval trigger renders in the settings triggers tab',
  }),
  Object.freeze({
    spec: 'e2e/agent-live-quality.spec.ts',
    title: 'follows a bounded instruction through a live model chat',
  }),
])

export const LIVE_E2E_SPECS = Object.freeze([...new Set(LIVE_E2E_CASES.map(({ spec }) => spec))])
export const LIVE_E2E_SPEC_GLOBS = Object.freeze(
  LIVE_E2E_SPECS.map((spec) => `**/${spec.slice('e2e/'.length)}`),
)

function hasNonEmptyValue(environment, name) {
  return typeof environment[name] === 'string' && environment[name].trim().length > 0
}

export function assertE2ELaneNodeVersion(version) {
  if (!/^v?22(?:\.|$)/.test(version)) {
    throw new Error('E2E lanes require Node.js 22.')
  }
}

export function resolveE2EProject(lane, project) {
  const projects = LANE_PROJECTS[lane]
  if (!projects) throw new Error('E2E lane must be either "scripted" or "live".')
  const resolvedProject = project || DEFAULT_PROJECTS[lane]
  if (!E2E_PROJECTS.includes(resolvedProject))
    throw new Error(`unknown E2E project: ${resolvedProject}`)
  if (!projects.includes(resolvedProject)) {
    throw new Error(`E2E project ${resolvedProject} is not valid for the ${lane} lane.`)
  }
  return resolvedProject
}

export function getLaneDefaultPorts(lane) {
  if (lane === 'scripted') return { frontend: '3100', backend: '8101' }
  if (lane === 'live') return { frontend: '3200', backend: '8201' }
  throw new Error('E2E lane must be either "scripted" or "live".')
}

export function normalizePlaywrightArguments(arguments_) {
  return arguments_[0] === '--' ? arguments_.slice(1) : arguments_
}

function readOptionValue(arguments_, index, option) {
  const argument = arguments_[index]
  if (argument === option) {
    const value = arguments_[index + 1]
    if (!value || value.startsWith('-')) throw new Error(`Playwright ${option} requires a value.`)
    return { value, nextIndex: index + 1 }
  }
  return { value: argument.slice(`${option}=`.length), nextIndex: index }
}

export function validatePlaywrightCliArguments(lane, expectedProject, rawArguments, selectionOnly) {
  const arguments_ = rawArguments[0] === 'test' ? rawArguments.slice(1) : rawArguments
  let selectedProject
  let sawList = false

  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index]
    if (argument === '--project' || argument.startsWith('--project=')) {
      const option = readOptionValue(arguments_, index, '--project')
      if (selectedProject) throw new Error('Playwright accepts exactly one --project selection.')
      selectedProject = option.value
      index = option.nextIndex
      continue
    }
    if (argument === '--workers' || argument.startsWith('--workers=')) {
      const option = readOptionValue(arguments_, index, '--workers')
      if (option.value !== '1') throw new Error('The E2E lane requires Playwright --workers=1.')
      index = option.nextIndex
      continue
    }
    if (argument === '--retries' || argument.startsWith('--retries=')) {
      const option = readOptionValue(arguments_, index, '--retries')
      if (option.value !== '0') throw new Error('The E2E lane requires Playwright --retries=0.')
      index = option.nextIndex
      continue
    }
    if (argument === '--reporter' || argument.startsWith('--reporter=')) {
      const option = readOptionValue(arguments_, index, '--reporter')
      if (!selectionOnly || option.value !== 'json') {
        throw new Error('The E2E lane does not allow --reporter overrides.')
      }
      index = option.nextIndex
      continue
    }
    if (argument === '--list') {
      if (!selectionOnly) throw new Error('The E2E lane does not allow --list during execution.')
      sawList = true
      continue
    }
    if (
      lane === 'scripted' &&
      /^e2e\/.+\.spec\.ts$/.test(argument) &&
      argument === path.posix.normalize(argument)
    ) {
      continue
    }
    if (argument.startsWith('-')) throw new Error(`The E2E lane does not allow ${argument}.`)
    if (lane === 'scripted') {
      throw new Error('The E2E lane requires normalized scripted spec selection beneath e2e/.')
    }
    throw new Error('The E2E lane does not allow direct spec or title selection.')
  }

  if (selectedProject !== expectedProject) {
    throw new Error(`The ${lane} E2E lane requires --project=${expectedProject}.`)
  }
  if (selectionOnly && !sawList) throw new Error('Selection-only E2E inspection requires --list.')
}

export function resolveConfiguredE2EProject(lane, environment, rawArguments) {
  const project = resolveE2EProject(lane, environment.E2E_PROJECT)
  if (!hasNonEmptyValue(environment, 'TEST_WORKER_INDEX')) {
    validatePlaywrightCliArguments(
      lane,
      project,
      rawArguments,
      environment.E2E_SELECTION_ONLY === '1',
    )
  }
  return project
}

export function buildExactLiveTitleFilter(cases = LIVE_E2E_CASES) {
  if (cases.length === 0) throw new Error('At least one live E2E case is required.')
  const escapedTitles = cases.map(({ title }) => title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  return escapedTitles.length === 1 ? `${escapedTitles[0]}$` : `(?:${escapedTitles.join('|')})$`
}

export function buildFrontendWebServerCommand(chatRuntime, apiBaseURL, frontendPort) {
  const runtimeEnvironment = `NEXT_PUBLIC_CHAT_RUNTIME=${chatRuntime} NEXT_PUBLIC_API_BASE_URL=${apiBaseURL}`
  return `pnpm prepare:assets && ${runtimeEnvironment} pnpm exec next build --webpack && ${runtimeEnvironment} pnpm exec next start --port ${frontendPort}`
}

export function buildBackendWebServerCommand(
  lane,
  skillEvaluationEnabled,
  corsOrigins,
  backendPort,
) {
  getLaneDefaultPorts(lane)
  const scriptedModelEnabled = lane === 'scripted' ? 'true' : 'false'
  return `E2E_SCRIPTED_MODEL_ENABLED=${scriptedModelEnabled} SKILL_EVALUATION_ENABLED=${skillEvaluationEnabled ? 'true' : 'false'} CORS_ALLOWED_ORIGINS=${corsOrigins} "\${MOLDY_GATE_PYTHON:-./.venv/bin/python}" -m uvicorn app.main:app --port ${backendPort}`
}

/**
 * @typedef {Readonly<{
 *   browserName: 'chromium'
 *   trace?: 'off'
 *   screenshot?: 'off'
 * }>} PlaywrightProjectUse
 */

/** @returns {PlaywrightProjectUse} */
export function buildPlaywrightProjectUse(project) {
  if (project === 'scripted-capture') return { browserName: 'chromium' }
  return { browserName: 'chromium', trace: 'off', screenshot: 'off' }
}

export function getPlaywrightExecutionPolicy() {
  return { workers: 1, retries: 0 }
}

export function getPlaywrightWebServerTimeout() {
  return 180_000
}

export function validateCaptureTour(lane, project, environment) {
  if (!hasNonEmptyValue(environment, 'E2E_CAPTURE_TOUR')) return
  if (environment.E2E_CAPTURE_TOUR !== '1') {
    throw new Error('E2E_CAPTURE_TOUR=1 is the only supported capture tour opt-in.')
  }
  if (lane !== 'scripted' || project !== 'scripted-capture') {
    throw new Error('E2E_CAPTURE_TOUR is only valid for scripted-capture.')
  }
}

export function copySafeEnvironment(inheritedEnvironment, lane) {
  const environment = {}
  const allowedNames =
    lane === 'live'
      ? [...SAFE_ENVIRONMENT_NAMES, ...REQUIRED_LIVE_LLM_VARIABLES]
      : SAFE_ENVIRONMENT_NAMES
  for (const name of allowedNames) {
    if (hasNonEmptyValue(inheritedEnvironment, name)) environment[name] = inheritedEnvironment[name]
  }
  return environment
}

export function buildE2ELauncherEnvironment(lane, project, inheritedEnvironment) {
  const e2eProject = resolveE2EProject(lane, project)
  validateCaptureTour(lane, e2eProject, inheritedEnvironment)
  const environment = {}
  for (const name of LAUNCHER_ENVIRONMENT_NAMES) {
    if (hasNonEmptyValue(inheritedEnvironment, name)) environment[name] = inheritedEnvironment[name]
  }
  if (hasNonEmptyValue(inheritedEnvironment, 'NEXT_PUBLIC_CHAT_RUNTIME')) {
    const runtime = inheritedEnvironment.NEXT_PUBLIC_CHAT_RUNTIME
    if (runtime !== 'legacy' && runtime !== 'langgraph_v3') {
      throw new Error('NEXT_PUBLIC_CHAT_RUNTIME must be legacy or langgraph_v3.')
    }
    environment.NEXT_PUBLIC_CHAT_RUNTIME = runtime
  }
  if (lane === 'live') {
    if (
      !REQUIRED_LIVE_LLM_VARIABLES.every((name) => hasNonEmptyValue(inheritedEnvironment, name))
    ) {
      throw new Error(
        'The live E2E lane requires non-empty E2E_LLM_BASE_URL, E2E_LLM_API_KEY, and E2E_LLM_MODEL.',
      )
    }
    for (const name of REQUIRED_LIVE_LLM_VARIABLES) environment[name] = inheritedEnvironment[name]
    if (inheritedEnvironment.E2E_EGRESS_ALLOW_OWNED_LOOPBACK === '1') {
      environment.E2E_EGRESS_ALLOW_OWNED_LOOPBACK = '1'
    }
  }
  return environment
}
