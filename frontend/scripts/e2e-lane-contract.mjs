import { existsSync, lstatSync, realpathSync } from 'node:fs'
import path from 'node:path'

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
  return getE2ERunPaths(lane, environment).authStatePath
}

function requirePreparedDirectory(runRoot, candidate) {
  const rootMetadata = lstatSync(runRoot)
  if (rootMetadata.isSymbolicLink() || !rootMetadata.isDirectory()) {
    throw new Error('MOLDY_TEST_RUN_ROOT must be a prepared directory.')
  }
  const resolvedRoot = realpathSync(runRoot)
  const unresolved = path.resolve(path.isAbsolute(candidate) ? candidate : path.join(runRoot, candidate))
  const suffix = []
  let ancestor = unresolved
  while (!existsSync(ancestor)) {
    const parent = path.dirname(ancestor)
    if (parent === ancestor) {
      throw new Error('Frontend lane path must remain beneath MOLDY_TEST_RUN_ROOT.')
    }
    suffix.unshift(path.basename(ancestor))
    ancestor = parent
  }
  const canonicalCandidate = path.join(realpathSync(ancestor), ...suffix)
  const relative = path.relative(resolvedRoot, canonicalCandidate)
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error('Frontend lane path must remain beneath MOLDY_TEST_RUN_ROOT.')
  }
  let current = resolvedRoot
  for (const component of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, component)
    const metadata = lstatSync(current)
    if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
      throw new Error('Frontend lane path components must be prepared directories.')
    }
  }
  const resolved = realpathSync(current)
  if (resolved !== resolvedRoot && !resolved.startsWith(`${resolvedRoot}${path.sep}`)) {
    throw new Error('Frontend lane path must remain beneath MOLDY_TEST_RUN_ROOT.')
  }
  return resolved
}

function resolvePreparedFile(runRoot, candidate) {
  const parent = requirePreparedDirectory(runRoot, path.dirname(candidate))
  const resolved = path.join(parent, path.basename(candidate))
  if (existsSync(resolved) && lstatSync(resolved).isSymbolicLink()) {
    throw new Error('Frontend lane file must not be a symbolic link.')
  }
  return resolved
}

export function getE2ERunPaths(lane, environment) {
  getLaneDefaultPorts(lane)
  const runRoot = hasNonEmptyValue(environment, 'MOLDY_TEST_RUN_ROOT')
    ? environment.MOLDY_TEST_RUN_ROOT
    : undefined
  if (!runRoot) {
    return {
      authStatePath: hasNonEmptyValue(environment, 'E2E_AUTH_STATE_PATH')
        ? environment.E2E_AUTH_STATE_PATH
        : `./e2e/.auth/${lane}-user.json`,
      buildDir: hasNonEmptyValue(environment, 'E2E_NEXT_BUILD_DIR') ? environment.E2E_NEXT_BUILD_DIR : '.next',
      resultsDir: hasNonEmptyValue(environment, 'E2E_RESULTS_DIR')
        ? environment.E2E_RESULTS_DIR
        : `test-results/${lane}`,
    }
  }
  const frontendRoot = path.join(runRoot, 'frontend')
  return {
    authStatePath: resolvePreparedFile(
      runRoot,
      hasNonEmptyValue(environment, 'E2E_AUTH_STATE_PATH')
        ? environment.E2E_AUTH_STATE_PATH
        : path.join(frontendRoot, 'auth', `${lane}-user.json`),
    ),
    buildDir: requirePreparedDirectory(
      runRoot,
      hasNonEmptyValue(environment, 'E2E_NEXT_BUILD_DIR')
        ? environment.E2E_NEXT_BUILD_DIR
        : path.join(frontendRoot, 'next', lane),
    ),
    resultsDir: requirePreparedDirectory(
      runRoot,
      hasNonEmptyValue(environment, 'E2E_RESULTS_DIR')
        ? environment.E2E_RESULTS_DIR
        : path.join(frontendRoot, 'test-results', lane),
    ),
  }
}

export function buildLaneEnvironment(lane, inheritedEnvironment) {
  const environment = { ...inheritedEnvironment, E2E_LANE: lane }
  const defaultPorts = getLaneDefaultPorts(lane)
  if (!hasNonEmptyValue(environment, 'E2E_FRONTEND_PORT')) environment.E2E_FRONTEND_PORT = defaultPorts.frontend
  if (!hasNonEmptyValue(environment, 'E2E_BACKEND_PORT')) environment.E2E_BACKEND_PORT = defaultPorts.backend
  environment.E2E_AUTH_STATE_PATH = getE2EAuthStatePath(lane, environment)
  const runPaths = getE2ERunPaths(lane, environment)
  environment.E2E_NEXT_BUILD_DIR = runPaths.buildDir
  environment.E2E_RESULTS_DIR = runPaths.resultsDir

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
