import { spawnSync } from 'node:child_process'
import { existsSync, lstatSync, realpathSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const LOCAL_REPOSITORY_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const SOURCE_BACKEND_ROOT = path.resolve(
  process.env.MOLDY_BACKEND_SOURCE_ROOT ?? path.join(LOCAL_REPOSITORY_ROOT, 'backend'),
)
const NATIVE_HELPER = path.resolve(SOURCE_BACKEND_ROOT, '../scripts/e2e_cleanup_export_paths.py')
const NATIVE_PYTHON = path.join(SOURCE_BACKEND_ROOT, '.venv/bin/python')
const MAX_NATIVE_OUTPUT = 72 * 1024 * 1024

function fail(message) {
  throw new Error(message)
}

function nativeRequest(request, message) {
  const result = spawnSync(NATIVE_PYTHON, [NATIVE_HELPER], {
    encoding: 'utf8',
    input: JSON.stringify(request),
    env: process.env,
    maxBuffer: MAX_NATIVE_OUTPUT,
  })
  if (result.status !== 0) {
    let reason
    try {
      reason = JSON.parse(result.stdout).error
    } catch {
      fail(message)
    }
    const failures = {
      symbolic_link: 'Artifact source must not contain symbolic links.',
      hard_link: 'Artifact source must not contain hard-linked files.',
      regular_file: 'Artifact source may only contain regular files.',
      export_file_size: 'E2E artifact size limit exceeded.',
      export_destination_exists:
        request.command === 'receipt'
          ? 'E2E export receipt destination is unsafe.'
          : 'E2E export destination already exists.',
    }
    fail(failures[reason] ?? message)
  }
  try {
    return JSON.parse(result.stdout)
  } catch {
    fail(message)
  }
}

function isContained(root, candidate) {
  const relative = path.relative(root, candidate)
  return relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)
}

function lstatDirectory(directory, message) {
  const metadata = lstatSync(directory)
  if (metadata.isSymbolicLink() || !metadata.isDirectory()) fail(message)
}

function canonicalMissingPath(target) {
  const missing = []
  let current = path.resolve(target)
  while (!existsSync(current)) {
    missing.unshift(path.basename(current))
    current = path.dirname(current)
  }
  return path.join(realpathSync(current), ...missing)
}

export function preparedRoot(runRoot) {
  if (!path.isAbsolute(runRoot) || !path.basename(runRoot).startsWith('.moldy-test-run.')) {
    fail('MOLDY_TEST_RUN_ROOT must be a prepared directory.')
  }
  const root = realpathSync(runRoot)
  lstatDirectory(root, 'MOLDY_TEST_RUN_ROOT must be a prepared directory.')
  lstatDirectory(path.join(root, 'frontend'), 'MOLDY_TEST_RUN_ROOT must be a prepared directory.')
  return root
}

export function artifactSource(runRoot, project, sourceDirectory) {
  const source = realpathSync(sourceDirectory)
  const candidates = [
    ['results', path.join(runRoot, 'frontend', 'test-results', project)],
    ['captures', path.join(runRoot, 'output', 'captures')],
    ['legacy-captures', path.join(runRoot, 'output', 'e2e-captures')],
  ]
  const matched = candidates.find(([, expected]) => source === expected)
  if (!isContained(runRoot, source) || !matched)
    fail('Artifact source must be a prepared capture subtree.')
  return {
    kind: matched[0],
    source,
    relative: path.relative(runRoot, source).split(path.sep).join('/'),
  }
}

export function canonicalDirectory(directory, message) {
  try {
    const canonical = realpathSync(directory)
    lstatDirectory(canonical, message)
    return canonical
  } catch {
    fail(message)
  }
}

export function listSafeFiles(runRoot, sources) {
  return nativeRequest(
    {
      command: 'list',
      root: runRoot,
      sources: sources.map(({ kind, relative }) => ({ kind, relative })),
    },
    'Artifact source changed during export.',
  ).files
}

export function readSafeFiles(runRoot, sources, selected) {
  const response = nativeRequest(
    {
      command: 'read',
      root: runRoot,
      sources: sources.map(({ kind, relative }) => ({ kind, relative })),
      selected,
    },
    'Artifact source changed during export.',
  )
  return new Map(
    response.files.map((file) => [
      `${file.kind}/${file.path}`,
      Buffer.from(file.content, 'base64'),
    ]),
  )
}

export function publishArtifactTree(repositoryRoot, destinationRelative, files) {
  const destination = path.join(repositoryRoot, ...destinationRelative.split('/'))
  if (existsSync(destination)) fail('E2E export destination already exists.')
  nativeRequest(
    {
      command: 'publish',
      root: repositoryRoot,
      destination: destinationRelative,
      files: files.map(({ path: artifactPath, content }) => ({
        path: artifactPath,
        content: content.toString('base64'),
      })),
    },
    'E2E export destination is unsafe.',
  )
}

export function removeArtifactTree(repositoryRoot, destinationRelative) {
  nativeRequest(
    { command: 'remove', root: repositoryRoot, destination: destinationRelative },
    'E2E export destination is unsafe.',
  )
}

export function writeReceipt(runRoot, target, payload) {
  const resolved = canonicalMissingPath(target)
  if (!isContained(runRoot, resolved) || existsSync(resolved)) {
    fail('E2E export receipt destination is unsafe.')
  }
  nativeRequest(
    {
      command: 'receipt',
      root: runRoot,
      target: path.relative(runRoot, resolved).split(path.sep).join('/'),
      payload: Buffer.from(payload).toString('base64'),
    },
    'E2E export receipt destination is unsafe.',
  )
}
