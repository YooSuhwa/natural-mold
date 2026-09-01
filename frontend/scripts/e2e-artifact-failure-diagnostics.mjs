import { E2E_SECRET_SCAN_RULE_IDS, SecretScanFailure } from './e2e-artifact-secret-rules.mjs'

const MAX_FAILURE_DIAGNOSTICS = 16
const MAX_NODE_ID_LENGTH = 2048
const FAILURE_STATUSES = new Set(['failed', 'timedOut', 'interrupted'])
const RULE_IDS = new Set(E2E_SECRET_SCAN_RULE_IDS)
const SAFE_SPEC = /^e2e\/[A-Za-z0-9][A-Za-z0-9._/-]*\.spec\.ts$/
const SAFE_PATH_COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const SOURCE_KINDS = new Set(['results', 'captures', 'legacy-captures'])

function fail() {
  throw new Error('E2E export failure diagnostics must be a bounded sanitized JSON array.')
}

function safeNodeId(value, project) {
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    value.length > MAX_NODE_ID_LENGTH ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return false
  }
  const prefix = `${project}::`
  if (!value.startsWith(prefix)) return false
  const titleSeparator = value.indexOf('::', prefix.length)
  if (titleSeparator < 0 || titleSeparator === value.length - 2) return false
  const spec = value.slice(prefix.length, titleSeparator)
  return SAFE_SPEC.test(spec) && !spec.split('/').includes('..')
}

function safeArtifactPath(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length > 1024) return false
  const parts = value.split('/')
  return (
    parts.length >= 2 &&
    parts.length <= 33 &&
    SOURCE_KINDS.has(parts[0]) &&
    parts.slice(1).every((part) => SAFE_PATH_COMPONENT.test(part))
  )
}

export class ArtifactSecretScanFailure extends Error {
  constructor(ruleId, artifactPath) {
    if (!RULE_IDS.has(ruleId) || !safeArtifactPath(artifactPath)) fail()
    super('E2E artifact secret scan failed.')
    this.name = 'ArtifactSecretScanFailure'
    this.ruleId = ruleId
    this.artifactPath = artifactPath
  }
}

export function scanSelectedArtifact(scan, artifactPath) {
  try {
    scan()
  } catch (error) {
    if (error instanceof SecretScanFailure && RULE_IDS.has(error.ruleId)) {
      throw new ArtifactSecretScanFailure(error.ruleId, artifactPath)
    }
    throw error
  }
}

export function parseFailureDiagnostics(raw, project) {
  if (raw === undefined || raw === '') return []
  let decoded = raw
  if (typeof raw === 'string') {
    try {
      decoded = JSON.parse(raw)
    } catch {
      fail()
    }
  }
  if (!Array.isArray(decoded) || decoded.length === 0 || decoded.length > MAX_FAILURE_DIAGNOSTICS)
    fail()
  const seen = new Set()
  return decoded.map((value) => {
    if (
      !value ||
      typeof value !== 'object' ||
      Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== 'node_id,status' ||
      !safeNodeId(value.node_id, project) ||
      !FAILURE_STATUSES.has(value.status) ||
      seen.has(value.node_id)
    ) {
      fail()
    }
    seen.add(value.node_id)
    return { node_id: value.node_id, status: value.status }
  })
}

export function sourceRejection(error, tests) {
  if (!(error instanceof ArtifactSecretScanFailure) || tests.length === 0) throw error
  return {
    category: 'secret_scan',
    rule_id: error.ruleId,
    artifact_path: error.artifactPath,
    tests,
  }
}
