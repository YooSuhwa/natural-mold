const REQUIRED_LIVE_LLM_VARIABLES = Object.freeze([
  'E2E_LLM_BASE_URL',
  'E2E_LLM_API_KEY',
  'E2E_LLM_MODEL',
])

export const LIVE_E2E_SPECS = Object.freeze([
  'e2e/builder.spec.ts',
  'e2e/operator-screens.spec.ts',
  'e2e/agent-triggers.spec.ts',
])

export const LIVE_E2E_TEST_MATCH = Object.freeze(
  LIVE_E2E_SPECS.map((spec) => `**/${spec.slice('e2e/'.length)}`),
)

export function getLaneDefaultPorts(lane) {
  if (lane === 'scripted') return { frontend: '3100', backend: '8101' }
  if (lane === 'live') return { frontend: '3200', backend: '8201' }
  throw new Error('E2E lane must be either "scripted" or "live".')
}

export function normalizePlaywrightArguments(arguments_) {
  return arguments_[0] === '--' ? arguments_.slice(1) : arguments_
}

function hasNonEmptyValue(environment, name) {
  return typeof environment[name] === 'string' && environment[name].trim().length > 0
}

export function getE2EAuthStatePath(lane, environment) {
  getLaneDefaultPorts(lane)
  if (hasNonEmptyValue(environment, 'E2E_AUTH_STATE_PATH')) return environment.E2E_AUTH_STATE_PATH
  return `./e2e/.auth/${lane}-user.json`
}

export function buildLaneEnvironment(lane, inheritedEnvironment) {
  const environment = { ...inheritedEnvironment, E2E_LANE: lane }
  const defaultPorts = getLaneDefaultPorts(lane)
  if (!hasNonEmptyValue(environment, 'E2E_FRONTEND_PORT')) environment.E2E_FRONTEND_PORT = defaultPorts.frontend
  if (!hasNonEmptyValue(environment, 'E2E_BACKEND_PORT')) environment.E2E_BACKEND_PORT = defaultPorts.backend
  environment.E2E_AUTH_STATE_PATH = getE2EAuthStatePath(lane, environment)

  if (lane === 'scripted') {
    environment.E2E_SCRIPTED_MODEL_ENABLED = 'true'
    delete environment.E2E_LLM_BASE_URL
    delete environment.E2E_LLM_API_KEY
    delete environment.E2E_LLM_MODEL
    return environment
  }

  if (lane === 'live') {
    if (!REQUIRED_LIVE_LLM_VARIABLES.every((name) => hasNonEmptyValue(environment, name))) {
      throw new Error(
        'The live E2E lane requires non-empty E2E_LLM_BASE_URL, E2E_LLM_API_KEY, and E2E_LLM_MODEL.',
      )
    }
    environment.E2E_SCRIPTED_MODEL_ENABLED = 'false'
    return environment
  }

  throw new Error('E2E lane must be either "scripted" or "live".')
}

export function assertIsolatedDatabaseEnvironment(lane, environment) {
  const databasePrefix = `moldy_e2e_${lane}`
  if (!hasNonEmptyValue(environment, 'DATABASE_URL') || !hasNonEmptyValue(environment, 'DATABASE_URL_SYNC')) {
    throw new Error(
      'The E2E lane requires explicit DATABASE_URL and DATABASE_URL_SYNC values for an isolated database.',
    )
  }

  let asyncDatabase
  let syncDatabase
  try {
    asyncDatabase = new URL(environment.DATABASE_URL)
    syncDatabase = new URL(environment.DATABASE_URL_SYNC)
  } catch {
    throw new Error('The E2E lane database URLs must be valid connection URLs.')
  }

  if (asyncDatabase.protocol !== 'postgresql+asyncpg:') {
    throw new Error('DATABASE_URL must use the postgresql+asyncpg driver.')
  }
  if (syncDatabase.protocol !== 'postgresql:') {
    throw new Error('DATABASE_URL_SYNC must use postgresql:// for the checkpointer.')
  }

  const asyncTarget = `${asyncDatabase.hostname}:${asyncDatabase.port || '5432'}${asyncDatabase.pathname}`
  const syncTarget = `${syncDatabase.hostname}:${syncDatabase.port || '5432'}${syncDatabase.pathname}`
  if (asyncTarget !== syncTarget) {
    throw new Error('DATABASE_URL and DATABASE_URL_SYNC must target the same host, port, and database.')
  }

  const databaseName = decodeURIComponent(asyncDatabase.pathname.slice(1))
  if (databaseName !== databasePrefix && !databaseName.startsWith(`${databasePrefix}_`)) {
    throw new Error(`The ${lane} E2E lane database must be ${databasePrefix} or start with ${databasePrefix}_.`)
  }
}
