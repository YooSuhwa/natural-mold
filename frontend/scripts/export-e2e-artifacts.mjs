#!/usr/bin/env node

import { createHash } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  artifactSource,
  canonicalDirectory,
  listSafeFiles,
  preparedRoot,
  publishArtifactTree,
  readSafeFiles,
  removeArtifactTree,
  writeReceipt,
} from './e2e-artifact-safe-fs.mjs'
import {
  classifyArtifact,
  dateInSeoul,
  E2E_EXPORT_PROJECTS,
  validateSlug,
} from './e2e-artifact-policy.mjs'
import { scanArtifactContent, scanTraceZip, validateSecrets } from './e2e-artifact-secret-scan.mjs'

export { E2E_EXPORT_PROJECTS }

// prettier-ignore
export const E2E_EXPORT_FAILURE_CATEGORIES = Object.freeze(['source_topology', 'unsupported_artifact', 'secret_scan', 'bounds', 'manifest_publish', 'internal'])

const EXPORT_MANIFEST_NAME = 'export-manifest.json'
const [EXPORT_POLICY_VERSION, EXPORT_SCHEMA_VERSION] = [1, 1]
const [MAX_FILE_BYTES, MAX_TOTAL_BYTES] = [20 * 1024 * 1024, 50 * 1024 * 1024]
const FAILURE_PREFIX = 'E2E_ARTIFACT_EXPORT_FAILURE:'
// prettier-ignore
const FAILURE_RULES = Object.freeze([
  ['secret_scan', /^E2E artifact secret scan failed\.$/],
  ['bounds', /^(?:E2E artifact size limit exceeded\.|Playwright trace archive exceeds the scan limit\.|E2E export secret values must be a bounded JSON string array\.|E2E_EXPORT_SLUG must be a bounded lowercase-safe slug\.)$/],
  ['manifest_publish', /^(?:E2E export repository root is invalid\.|E2E export destination (?:is unsafe|already exists)\.|E2E export receipt destination is unsafe\.)$/],
  ['unsupported_artifact', /^(?:Suspicious E2E artifact is outside the explicit allowlist\.|Screenshots may only be exported from the scripted-capture project\.|Playwright trace archive is invalid\.|Artifact source may only contain (?:regular files|unlinked regular files)\.)$/],
  ['source_topology', /^(?:MOLDY_TEST_RUN_ROOT must be a prepared directory\.|Artifact source must (?:remain in the prepared capture subtree|be a prepared capture subtree|not contain symbolic links|not contain hard-linked files)\.|Artifact source changed during export\.|E2E artifact path is outside the explicit topology policy\.|E2E artifact sources must be unique\.|At least one E2E artifact source is required\.|E2E export project is invalid\.|E2E export secret values must be a JSON array in E2E_EXPORT_SECRETS_JSON\.|Usage: export-e2e-artifacts\.mjs .*)$/],
])

class ExportPublishFailure extends Error {
  constructor() {
    super('E2E export publish failed.')
  }
}

export function classifyExportFailure(error) {
  if (error instanceof ExportPublishFailure) return 'manifest_publish'
  if (!(error instanceof Error)) return 'internal'
  return FAILURE_RULES.find(([, pattern]) => pattern.test(error.message))?.[0] ?? 'internal'
}

function fail(message) {
  throw new Error(message)
}

function sha256(content) {
  return createHash('sha256').update(content).digest('hex')
}

function collectArtifacts(runRoot, sources, secrets, project) {
  const artifacts = []
  let total = 0
  const selected = []
  const classifications = new Map()
  for (const file of listSafeFiles(runRoot, sources)) {
    const classification = classifyArtifact(file.kind, file.path, project)
    if (!classification) continue
    selected.push(file)
    classifications.set(`${file.kind}/${file.path}`, classification)
  }
  const contentByPath = readSafeFiles(runRoot, sources, selected)
  for (const file of selected) {
    const artifactPath = `${file.kind}/${file.path}`
    const content = contentByPath.get(artifactPath)
    const classification = classifications.get(artifactPath)
    if (!content || !classification) fail('Artifact source changed during export.')
    total += content.length
    if (total > MAX_TOTAL_BYTES) fail('E2E artifact size limit exceeded.')
    if (classification.trace) scanTraceZip(content, secrets)
    else scanArtifactContent(content, secrets)
    artifacts.push({
      path: artifactPath,
      content,
      sha256: sha256(content),
      size_bytes: content.length,
      screenshot: classification.screenshot,
    })
  }
  return artifacts.sort((left, right) => left.path.localeCompare(right.path))
}

function buildManifest(project, artifacts, exactSecretCount) {
  return {
    schema_version: EXPORT_SCHEMA_VERSION,
    project,
    policy: {
      version: EXPORT_POLICY_VERSION,
      screenshots: 'scripted-capture-only',
      max_file_bytes: MAX_FILE_BYTES,
      max_total_bytes: MAX_TOTAL_BYTES,
    },
    secret_scan: { passed: true, exact_secret_count: exactSecretCount },
    files: artifacts.map(({ path: artifactPath, sha256: hash, size_bytes: size }) => ({
      path: artifactPath,
      sha256: hash,
      size_bytes: size,
    })),
    total: {
      file_count: artifacts.length,
      size_bytes: artifacts.reduce((sum, artifact) => sum + artifact.size_bytes, 0),
    },
  }
}

export function exportE2EArtifacts(options) {
  const environment = options.environment ?? process.env
  const project = options.project ?? environment.E2E_PROJECT
  if (!E2E_EXPORT_PROJECTS.includes(project)) fail('E2E export project is invalid.')
  const runRoot = preparedRoot(options.runRoot ?? environment.MOLDY_TEST_RUN_ROOT ?? '')
  const rawSources = options.sourceDirectories ?? [
    options.sourceDirectory ?? path.join(runRoot, 'output', 'captures'),
  ]
  if (!Array.isArray(rawSources) || rawSources.length === 0)
    fail('At least one E2E artifact source is required.')
  const sources = rawSources.map((source) => artifactSource(runRoot, project, source))
  if (new Set(sources.map((source) => source.kind)).size !== sources.length)
    fail('E2E artifact sources must be unique.')
  const slug = validateSlug(options.slug ?? environment.E2E_EXPORT_SLUG)
  const secrets = validateSecrets(
    options.secrets ?? parseSecrets(environment.E2E_EXPORT_SECRETS_JSON),
  )
  const repositoryRoot = canonicalDirectory(
    path.resolve(
      options.repositoryRoot ??
        environment.MOLDY_REPOSITORY_ROOT ??
        path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..'),
    ),
    'E2E export repository root is invalid.',
  )
  const date = dateInSeoul(options.now ?? new Date())
  const name = `${date.year}${date.month}${date.day}-${slug}`
  const destinationRelative = path.posix.join('output', 'e2e-captures', name)
  const artifacts = collectArtifacts(runRoot, sources, secrets, project)
  const manifestContent = Buffer.from(
    `${JSON.stringify(buildManifest(project, artifacts, secrets.length), null, 2)}\n`,
  )
  const manifestFile = {
    path: EXPORT_MANIFEST_NAME,
    sha256: sha256(manifestContent),
    size_bytes: manifestContent.length,
  }
  const receipt = {
    schema_version: EXPORT_SCHEMA_VERSION,
    secret_scan_passed: true,
    export_directory: destinationRelative,
    manifest: manifestFile,
    files: [
      manifestFile,
      ...artifacts.map(({ path: artifactPath, sha256: hash, size_bytes: size }) => ({
        path: artifactPath,
        sha256: hash,
        size_bytes: size,
      })),
    ],
    screenshots: artifacts
      .filter((artifact) => artifact.screenshot)
      .map((artifact) => artifact.path),
  }
  let published = false
  try {
    publishArtifactTree(repositoryRoot, destinationRelative, [
      ...artifacts,
      { path: EXPORT_MANIFEST_NAME, content: manifestContent },
    ])
    published = true
    if (options.receiptPath)
      writeReceipt(runRoot, path.resolve(options.receiptPath), `${JSON.stringify(receipt)}\n`)
    return receipt
  } catch (error) {
    if (published) removeArtifactTree(repositoryRoot, destinationRelative)
    if (classifyExportFailure(error) === 'internal') throw new ExportPublishFailure()
    throw error
  }
}

function parseSecrets(raw) {
  if (!raw) return []
  try {
    return validateSecrets(JSON.parse(raw))
  } catch (error) {
    if (error instanceof SyntaxError)
      fail('E2E export secret values must be a JSON array in E2E_EXPORT_SECRETS_JSON.')
    throw error
  }
}

function parseCli(arguments_) {
  const values = { sourceDirectories: [] }
  const names = {
    '--run-root': 'runRoot',
    '--project': 'project',
    '--slug': 'slug',
    '--repo-root': 'repositoryRoot',
    '--receipt': 'receiptPath',
  }
  for (let index = 0; index < arguments_.length; index += 2) {
    const flag = arguments_[index]
    const value = arguments_[index + 1]
    if (!value || (flag !== '--source-dir' && !names[flag]))
      fail(
        'Usage: export-e2e-artifacts.mjs --run-root DIR --source-dir DIR [--source-dir DIR] --project NAME --slug NAME --repo-root DIR [--receipt FILE]',
      )
    if (flag === '--source-dir') values.sourceDirectories.push(value)
    else values[names[flag]] = value
  }
  if (
    !values.runRoot ||
    !values.project ||
    !values.slug ||
    !values.repositoryRoot ||
    values.sourceDirectories.length === 0
  )
    fail(
      'Usage: export-e2e-artifacts.mjs --run-root DIR --source-dir DIR [--source-dir DIR] --project NAME --slug NAME --repo-root DIR [--receipt FILE]',
    )
  return values
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    process.stdout.write(`${JSON.stringify(exportE2EArtifacts(parseCli(process.argv.slice(2))))}\n`)
  } catch (error) {
    process.stderr.write(`${FAILURE_PREFIX}${classifyExportFailure(error)}\n`)
    process.exitCode = 1
  }
}
