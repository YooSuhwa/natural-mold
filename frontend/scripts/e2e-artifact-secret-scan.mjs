import { inflateRawSync } from 'node:zlib'

import { SecretScanFailure } from './e2e-artifact-secret-rules.mjs'

const MAX_DECODED_REPRESENTATION_BYTES = 5 * 1024 * 1024
const MAX_REPRESENTATION_DEPTH = 4
const MAX_STRUCTURED_DEPTH = 32
const MAX_STRUCTURED_NODES = 50_000
const MAX_ZIP_ENTRIES = 512
const MAX_ZIP_ENTRY_BYTES = 10 * 1024 * 1024
const MAX_ZIP_CONTENT_BYTES = 25 * 1024 * 1024

const SENSITIVE_NAMES =
  /^(?:authorization|cookie|set-cookie|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|csrf[_-]?token|session(?:[_-]?(?:id|token))?|moldy_(?:at|rt|csrf))$/i
const SENSITIVE_QUERY_NAMES =
  /^(?:key|api[_-]?key|token|access[_-]?token|auth|authorization|password|secret|client[_-]?secret|code|credential|signature|sig|session(?:[_-]?(?:id|token))?|x-amz-(?:credential|signature|security-token)|x-goog-(?:credential|signature))$/i
const REPRESENTATION_NAMES = /^(?:body|content|payload|request|response|text|value|data)$/i
const SECRET_PATTERNS = Object.freeze([
  ['private_key', /-----BEGIN (?:ENCRYPTED |RSA |EC |OPENSSH )?PRIVATE KEY-----/],
  ['bearer_token', /\bbearer\s+[a-z0-9._~+/=-]{8,}/i],
  [
    'sensitive_assignment',
    /\b(?:authorization|cookie|set-cookie|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|csrf[_-]?token|session[_-]?(?:id|token)|moldy_(?:at|rt|csrf))\s*[:=]\s*["']?[^\s"',;}]{8,}/i,
  ],
  ['credential_dsn', /\b(?:postgres(?:ql)?|mysql|redis):\/\/[^\s/:"'<>]+:[^\s/@"'<>]+@[^\s"'<>]+/i],
  ['jwt', /\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b/],
])

function failSecret(ruleId) {
  throw new SecretScanFailure(ruleId)
}

function failZip(message = 'Playwright trace archive is invalid.') {
  throw new Error(message)
}

function containsCredentialQuery(text) {
  const urls = text.match(/(?:https?:\/\/[^\s"'<>]+|\/[a-z0-9_./-]*)\?[^\s"'<>]+/gi) ?? []
  return urls.some((candidate) => {
    try {
      const parsed = new URL(candidate, 'https://artifact.invalid')
      return [...parsed.searchParams.keys()].some((key) => SENSITIVE_QUERY_NAMES.test(key))
    } catch {
      return false
    }
  })
}

function isSensitiveValue(value) {
  if (typeof value === 'string' || Array.isArray(value)) return value.length > 0
  if (value && typeof value === 'object') return Object.keys(value).length > 0
  return value !== null && value !== false && value !== undefined
}

function decodeBase64(value) {
  const normalized = value.replace(/\s/g, '')
  if (
    normalized.length < 24 ||
    normalized.length % 4 !== 0 ||
    !/^[a-z0-9+/]+={0,2}$/i.test(normalized)
  ) {
    return undefined
  }
  const decodedSize = Math.floor((normalized.length * 3) / 4)
  if (decodedSize > MAX_DECODED_REPRESENTATION_BYTES) failSecret('representation_bounds')
  const decoded = Buffer.from(normalized, 'base64')
  return decoded.toString('base64').replace(/=+$/, '') === normalized.replace(/=+$/, '')
    ? decoded
    : undefined
}

function scanStructured(value, state, parentName = '') {
  state.nodes += 1
  if (state.nodes > MAX_STRUCTURED_NODES || state.depth > MAX_STRUCTURED_DEPTH)
    failSecret('representation_bounds')
  if (typeof value === 'string') {
    scanTextValue(value, state, REPRESENTATION_NAMES.test(parentName))
    return
  }
  if (!value || typeof value !== 'object') return
  const next = { ...state, depth: state.depth + 1 }
  if (Array.isArray(value)) {
    for (const item of value) scanStructured(item, next, parentName)
    state.nodes = next.nodes
    state.decodedBytes = next.decodedBytes
    return
  }
  const entries = Object.entries(value)
  const nameEntry = entries.find(([key]) => key.toLowerCase() === 'name')
  const valueEntry = entries.find(([key]) => key.toLowerCase() === 'value')
  const cookieValue =
    /^cookies?$/i.test(parentName) && valueEntry && isSensitiveValue(valueEntry[1])
  const namedSensitiveValue =
    nameEntry &&
    valueEntry &&
    typeof nameEntry[1] === 'string' &&
    SENSITIVE_NAMES.test(nameEntry[1]) &&
    isSensitiveValue(valueEntry[1])
  if (cookieValue) failSecret('structured_cookie_value')
  if (namedSensitiveValue) failSecret('structured_sensitive_value')
  for (const [key, child] of entries) {
    if (SENSITIVE_NAMES.test(key) && isSensitiveValue(child))
      failSecret('structured_sensitive_value')
    scanStructured(child, next, key)
  }
  state.nodes = next.nodes
  state.decodedBytes = next.decodedBytes
}

function scanParsedRepresentations(text, state) {
  const candidates = [text]
  if (text.includes('\n'))
    candidates.push(...text.split(/\r?\n/).filter((line) => line.trimStart().startsWith('{')))
  for (const candidate of candidates.slice(0, 10_000)) {
    try {
      scanStructured(JSON.parse(candidate), state)
      if (candidate === text) return
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error
    }
  }
}

function scanTextValue(text, state, representation = false) {
  if (state.secrets.some((secret) => text.includes(secret))) failSecret('configured_exact_secret')
  for (const [ruleId, pattern] of SECRET_PATTERNS) {
    if (pattern.test(text)) failSecret(ruleId)
  }
  if (containsCredentialQuery(text)) failSecret('credential_query')
  if (state.representationDepth >= MAX_REPRESENTATION_DEPTH) return
  const shouldParse = representation || /^[\s]*[\[{]/.test(text)
  if (shouldParse) scanParsedRepresentations(text, state)
  const decoded = decodeBase64(text)
  if (!decoded) return
  state.decodedBytes += decoded.length
  if (state.decodedBytes > MAX_DECODED_REPRESENTATION_BYTES) failSecret('representation_bounds')
  scanTextValue(
    decoded.toString('utf8'),
    { ...state, representationDepth: state.representationDepth + 1 },
    true,
  )
}

export function validateSecrets(secrets) {
  if (
    !Array.isArray(secrets) ||
    secrets.length > 128 ||
    !secrets.every((value) => typeof value === 'string' && value.length > 0 && value.length <= 4096)
  ) {
    throw new Error('E2E export secret values must be a bounded JSON string array.')
  }
  return secrets
}

export function scanArtifactContent(content, secrets) {
  if (secrets.some((secret) => content.includes(Buffer.from(secret))))
    failSecret('configured_exact_secret')
  const state = { secrets, nodes: 0, depth: 0, decodedBytes: 0, representationDepth: 0 }
  const text = content.toString('utf8')
  scanTextValue(text, state, true)
  scanParsedRepresentations(text, state)
}

function checkedRange(content, offset, length) {
  if (
    !Number.isSafeInteger(offset) ||
    !Number.isSafeInteger(length) ||
    offset < 0 ||
    length < 0 ||
    offset + length > content.length
  ) {
    failZip()
  }
}

export function scanTraceZip(content, secrets) {
  const minimumEnd = Math.max(0, content.length - 65_557)
  const end = content.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]))
  if (end < minimumEnd || end + 22 > content.length) failZip()
  const commentLength = content.readUInt16LE(end + 20)
  if (end + 22 + commentLength !== content.length) failZip()
  const entries = content.readUInt16LE(end + 10)
  const diskEntries = content.readUInt16LE(end + 8)
  const centralSize = content.readUInt32LE(end + 12)
  const centralOffset = content.readUInt32LE(end + 16)
  if (entries !== diskEntries || entries > MAX_ZIP_ENTRIES || centralOffset + centralSize !== end)
    failZip()
  let offset = centralOffset
  let scanned = 0
  const metadata = [content.subarray(end + 22, end + 22 + commentLength)]
  for (let index = 0; index < entries; index += 1) {
    checkedRange(content, offset, 46)
    if (content.readUInt32LE(offset) !== 0x02014b50) failZip()
    const flags = content.readUInt16LE(offset + 8)
    const method = content.readUInt16LE(offset + 10)
    const compressedSize = content.readUInt32LE(offset + 20)
    const uncompressedSize = content.readUInt32LE(offset + 24)
    const nameLength = content.readUInt16LE(offset + 28)
    const extraLength = content.readUInt16LE(offset + 30)
    const entryCommentLength = content.readUInt16LE(offset + 32)
    const localOffset = content.readUInt32LE(offset + 42)
    if (
      (flags & 1) !== 0 ||
      ![0, 8].includes(method) ||
      compressedSize > MAX_ZIP_ENTRY_BYTES ||
      uncompressedSize > MAX_ZIP_ENTRY_BYTES
    ) {
      failZip('Playwright trace archive exceeds the scan limit.')
    }
    checkedRange(content, offset, 46 + nameLength + extraLength + entryCommentLength)
    checkedRange(content, localOffset, 30)
    if (
      content.readUInt32LE(localOffset) !== 0x04034b50 ||
      content.readUInt16LE(localOffset + 8) !== method
    )
      failZip()
    const localFlags = content.readUInt16LE(localOffset + 6)
    const localNameLength = content.readUInt16LE(localOffset + 26)
    const localExtraLength = content.readUInt16LE(localOffset + 28)
    if (localFlags !== flags || localNameLength !== nameLength) failZip()
    const centralNameStart = offset + 46
    const centralExtraStart = centralNameStart + nameLength
    const centralCommentStart = centralExtraStart + extraLength
    const centralName = content.subarray(centralNameStart, centralExtraStart)
    const centralExtra = content.subarray(centralExtraStart, centralCommentStart)
    const entryComment = content.subarray(
      centralCommentStart,
      centralCommentStart + entryCommentLength,
    )
    const localNameStart = localOffset + 30
    checkedRange(content, localNameStart, localNameLength + localExtraLength + compressedSize)
    const localExtraStart = localNameStart + localNameLength
    const localName = content.subarray(localNameStart, localExtraStart)
    const localExtra = content.subarray(localExtraStart, localExtraStart + localExtraLength)
    if (!centralName.equals(localName)) failZip()
    if ((flags & 8) === 0) {
      const duplicateFieldsMatch =
        content.readUInt32LE(localOffset + 14) === content.readUInt32LE(offset + 16) &&
        content.readUInt32LE(localOffset + 18) === compressedSize &&
        content.readUInt32LE(localOffset + 22) === uncompressedSize
      if (!duplicateFieldsMatch) failZip()
    }
    metadata.push(centralName, centralExtra, entryComment, localName, localExtra)
    const payloadStart = localExtraStart + localExtraLength
    if (payloadStart + compressedSize > centralOffset) failZip()
    const payload = content.subarray(payloadStart, payloadStart + compressedSize)
    let value
    try {
      value =
        method === 0 ? payload : inflateRawSync(payload, { maxOutputLength: MAX_ZIP_ENTRY_BYTES })
    } catch (error) {
      if (error instanceof Error) failZip('Playwright trace archive exceeds the scan limit.')
      throw error
    }
    if (value.length !== uncompressedSize || (scanned += value.length) > MAX_ZIP_CONTENT_BYTES) {
      failZip('Playwright trace archive exceeds the scan limit.')
    }
    scanArtifactContent(value, secrets)
    offset += 46 + nameLength + extraLength + entryCommentLength
  }
  if (offset !== end) failZip()
  for (const value of metadata) scanArtifactContent(value, secrets)
}
