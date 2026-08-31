import path from 'node:path'

import { getPreparedE2ERunPaths } from './e2e-lane-paths.mjs'
import {
  assertE2ELaneNodeVersion,
  buildBackendWebServerCommand,
  buildE2ELauncherEnvironment,
  buildExactLiveTitleFilter,
  buildFrontendWebServerCommand,
  buildPlaywrightProjectUse,
  copySafeEnvironment,
  E2E_PROJECTS,
  getLaneDefaultPorts,
  getPlaywrightExecutionPolicy,
  LIVE_E2E_CASES,
  LIVE_E2E_SPEC_GLOBS,
  LIVE_E2E_SPECS,
  normalizePlaywrightArguments,
  REQUIRED_LIVE_LLM_VARIABLES,
  resolveConfiguredE2EProject,
  resolveE2EProject,
  validateCaptureTour,
  validatePlaywrightCliArguments,
} from './e2e-lane-runtime.mjs'

export {
  assertE2ELaneNodeVersion,
  buildBackendWebServerCommand,
  buildE2ELauncherEnvironment,
  buildExactLiveTitleFilter,
  buildFrontendWebServerCommand,
  buildPlaywrightProjectUse,
  E2E_PROJECTS,
  getLaneDefaultPorts,
  getPlaywrightExecutionPolicy,
  LIVE_E2E_CASES,
  LIVE_E2E_SPEC_GLOBS,
  LIVE_E2E_SPECS,
  normalizePlaywrightArguments,
  resolveConfiguredE2EProject,
  resolveE2EProject,
  validatePlaywrightCliArguments,
}

function hasNonEmptyValue(environment, name) {
  return typeof environment[name] === 'string' && environment[name].trim().length > 0
}

export function getE2ERunPaths(lane, environment, project) {
  getLaneDefaultPorts(lane)
  const e2eProject = resolveE2EProject(lane, project ?? environment.E2E_PROJECT)
  return getPreparedE2ERunPaths(lane, environment, e2eProject)
}

export function getE2EAuthStatePath(lane, environment, project) {
  return getE2ERunPaths(lane, environment, project).authStatePath
}

export function getPlaywrightArtifactsDirectory(lane, environment, project) {
  return path.join(getE2ERunPaths(lane, environment, project).resultsDir, 'playwright-artifacts')
}

export function buildLaneEnvironment(lane, inheritedEnvironment, project) {
  const e2eProject = resolveE2EProject(lane, project ?? inheritedEnvironment.E2E_PROJECT)
  validateCaptureTour(lane, e2eProject, inheritedEnvironment)
  const environment = copySafeEnvironment(inheritedEnvironment, lane)
  environment.E2E_LANE = lane
  environment.E2E_PROJECT = e2eProject
  const defaultPorts = getLaneDefaultPorts(lane)
  if (!hasNonEmptyValue(environment, 'E2E_FRONTEND_PORT')) {
    environment.E2E_FRONTEND_PORT = defaultPorts.frontend
  }
  if (!hasNonEmptyValue(environment, 'E2E_BACKEND_PORT')) {
    environment.E2E_BACKEND_PORT = defaultPorts.backend
  }
  environment.E2E_AUTH_STATE_PATH = getE2EAuthStatePath(lane, environment, e2eProject)
  const runPaths = getE2ERunPaths(lane, environment, e2eProject)
  environment.E2E_NEXT_BUILD_DIR = runPaths.buildDir
  environment.E2E_RESULTS_DIR = runPaths.resultsDir

  if (lane === 'scripted') {
    environment.E2E_SCRIPTED_MODEL_ENABLED = 'true'
    delete environment.E2E_LLM_BASE_URL
    delete environment.E2E_LLM_API_KEY
    delete environment.E2E_LLM_MODEL
    return environment
  }
  if (!REQUIRED_LIVE_LLM_VARIABLES.every((name) => hasNonEmptyValue(environment, name))) {
    throw new Error(
      'The live E2E lane requires non-empty E2E_LLM_BASE_URL, E2E_LLM_API_KEY, and E2E_LLM_MODEL.',
    )
  }
  environment.E2E_SCRIPTED_MODEL_ENABLED = 'false'
  return environment
}

function hasLoopbackHostname(url) {
  return url.hostname === '127.0.0.1' || url.hostname === '::1' || url.hostname === 'localhost'
}

export function sanitizePlaywrightEnvironment(lane, inheritedEnvironment, project) {
  const selectionOnly = inheritedEnvironment.E2E_SELECTION_ONLY === '1'
  const sourceEnvironment =
    lane === 'live' && selectionOnly
      ? {
          ...inheritedEnvironment,
          E2E_LLM_BASE_URL: 'http://127.0.0.1:1/v1',
          E2E_LLM_API_KEY: 'selection-only',
          E2E_LLM_MODEL: 'selection-only',
        }
      : inheritedEnvironment
  const environment = buildLaneEnvironment(lane, sourceEnvironment, project)
  if (lane !== 'live' || selectionOnly) return environment

  let liveBaseUrl
  try {
    liveBaseUrl = new URL(environment.E2E_LLM_BASE_URL)
  } catch {
    throw new Error('The live E2E lane requires a valid local egress proxy URL.')
  }
  if (liveBaseUrl.protocol !== 'http:' || !hasLoopbackHostname(liveBaseUrl)) {
    throw new Error('The live E2E lane requires a local egress proxy URL.')
  }
  return environment
}

export function assertIsolatedDatabaseEnvironment(lane, environment) {
  const databasePrefix = `moldy_e2e_${lane}`
  if (
    !hasNonEmptyValue(environment, 'DATABASE_URL') ||
    !hasNonEmptyValue(environment, 'DATABASE_URL_SYNC')
  ) {
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
    throw new Error(
      'DATABASE_URL and DATABASE_URL_SYNC must target the same host, port, and database.',
    )
  }
  const databaseName = decodeURIComponent(asyncDatabase.pathname.slice(1))
  if (databaseName !== databasePrefix && !databaseName.startsWith(`${databasePrefix}_`)) {
    throw new Error(
      `The ${lane} E2E lane database must be ${databasePrefix} or start with ${databasePrefix}_.`,
    )
  }
}
