import { existsSync, lstatSync, realpathSync } from 'node:fs'
import path from 'node:path'

function requirePreparedDirectory(runRoot, candidate) {
  const rootMetadata = lstatSync(runRoot)
  if (rootMetadata.isSymbolicLink() || !rootMetadata.isDirectory()) {
    throw new Error('MOLDY_TEST_RUN_ROOT must be a prepared directory.')
  }
  const resolvedRoot = realpathSync(runRoot)
  const unresolved = path.resolve(
    path.isAbsolute(candidate) ? candidate : path.join(runRoot, candidate),
  )
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

function resolvePreparedDisposableDirectory(runRoot, candidate) {
  const parent = requirePreparedDirectory(runRoot, path.dirname(candidate))
  const resolved = path.join(parent, path.basename(candidate))
  if (!existsSync(resolved)) return resolved
  const metadata = lstatSync(resolved)
  if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
    throw new Error(
      'Frontend Playwright artifact directory must be a directory without symbolic links.',
    )
  }
  return realpathSync(resolved)
}

function hasNonEmptyValue(environment, name) {
  return typeof environment[name] === 'string' && environment[name].trim().length > 0
}

export function getPreparedE2ERunPaths(lane, environment, project) {
  const runRoot = hasNonEmptyValue(environment, 'MOLDY_TEST_RUN_ROOT')
    ? environment.MOLDY_TEST_RUN_ROOT
    : undefined
  if (!runRoot) {
    return {
      authStatePath: hasNonEmptyValue(environment, 'E2E_AUTH_STATE_PATH')
        ? environment.E2E_AUTH_STATE_PATH
        : `./e2e/.auth/${lane}-user.json`,
      buildDir: hasNonEmptyValue(environment, 'E2E_NEXT_BUILD_DIR')
        ? environment.E2E_NEXT_BUILD_DIR
        : '.next',
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
        : path.join(frontendRoot, 'auth', project, `${lane}-user.json`),
    ),
    buildDir: requirePreparedDirectory(
      runRoot,
      hasNonEmptyValue(environment, 'E2E_NEXT_BUILD_DIR')
        ? environment.E2E_NEXT_BUILD_DIR
        : path.join(frontendRoot, '.next', project),
    ),
    resultsDir: requirePreparedDirectory(
      runRoot,
      hasNonEmptyValue(environment, 'E2E_RESULTS_DIR')
        ? environment.E2E_RESULTS_DIR
        : path.join(frontendRoot, 'test-results', project),
    ),
  }
}

export function getPreparedPlaywrightArtifactsDirectory(lane, environment, project) {
  const runPaths = getPreparedE2ERunPaths(lane, environment, project)
  const runRoot = hasNonEmptyValue(environment, 'MOLDY_TEST_RUN_ROOT')
    ? environment.MOLDY_TEST_RUN_ROOT
    : undefined
  if (!runRoot || project === 'scripted-capture') {
    return path.join(runPaths.resultsDir, 'playwright-artifacts')
  }
  return resolvePreparedDisposableDirectory(
    runRoot,
    path.join(runRoot, 'frontend', 'playwright-artifacts', project),
  )
}
