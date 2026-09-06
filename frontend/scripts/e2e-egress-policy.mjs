import { randomBytes } from 'node:crypto'
import { closeSync, fsyncSync, linkSync, openSync, unlinkSync, writeFileSync } from 'node:fs'
import net, { isIP } from 'node:net'
import { basename, dirname, join } from 'node:path'
import tls from 'node:tls'

const ENCODED_PATH_CONTROL = /%(?:2e|2f|5c)/i
const SAFE_SEGMENT = /^[A-Za-z0-9._~-]+$/

export class E2EEgressPolicyError extends Error {
  constructor(code) {
    super(`E2E egress policy rejected the request (${code}).`)
    this.name = 'E2EEgressPolicyError'
    this.code = code
  }
}

function reject(code) {
  throw new E2EEgressPolicyError(code)
}

function stripIpv6Brackets(hostname) {
  return hostname.startsWith('[') && hostname.endsWith(']') ? hostname.slice(1, -1) : hostname
}

function parseIpv4(address) {
  const octets = address.split('.').map(Number)
  return octets.length === 4 &&
    octets.every((octet) => Number.isInteger(octet) && octet >= 0 && octet <= 255)
    ? octets
    : undefined
}

function isUnsafeIpv4(address) {
  const octets = parseIpv4(address)
  if (!octets) return true
  const [a, b] = octets
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 168 && b === 63 && octets[2] === 129 && octets[3] === 16) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && (b === 0 || b === 168)) ||
    (a === 198 && (b === 18 || b === 19)) ||
    a >= 224
  )
}

function mappedIpv4(address) {
  const normalized = address.toLowerCase()
  const dotted = normalized.match(/^(?:::ffff:)(\d+\.\d+\.\d+\.\d+)$/)
  if (dotted) return dotted[1]
  const hexadecimal = normalized.match(/^(?:::ffff:)([0-9a-f]{1,4}):([0-9a-f]{1,4})$/)
  if (!hexadecimal) return undefined
  const high = Number.parseInt(hexadecimal[1], 16)
  const low = Number.parseInt(hexadecimal[2], 16)
  return `${high >> 8}.${high & 255}.${low >> 8}.${low & 255}`
}

export function classifyAddress(address) {
  const unwrapped = stripIpv6Brackets(address).split('%', 1)[0]
  const family = isIP(unwrapped)
  if (family === 4) return { address: unwrapped, family, unsafe: isUnsafeIpv4(unwrapped) }
  if (family !== 6) reject('invalid_dns_address')
  const mapped = mappedIpv4(unwrapped)
  if (mapped) return { address: mapped, family: 4, unsafe: isUnsafeIpv4(mapped) }
  const normalized = unwrapped.toLowerCase()
  const unsafe =
    normalized === '::' ||
    normalized === '::1' ||
    normalized.startsWith('fc') ||
    normalized.startsWith('fd') ||
    /^fe[89ab]/.test(normalized) ||
    normalized.startsWith('ff')
  return { address: unwrapped, family, unsafe }
}

export function validateConfiguredBaseUrl(rawValue, options = {}) {
  if (typeof rawValue !== 'string' || rawValue.length === 0 || rawValue.trim() !== rawValue) {
    reject('noncanonical_url')
  }
  if (/[\\?#]/.test(rawValue) || ENCODED_PATH_CONTROL.test(rawValue)) reject('ambiguous_url')

  let url
  try {
    url = new URL(rawValue)
  } catch {
    reject('invalid_url')
  }
  if (url.username || url.password || url.search || url.hash) reject('url_components')
  if (url.pathname.includes('//')) reject('ambiguous_path')
  const authority = rawValue.slice(rawValue.indexOf('://') + 3).split('/', 1)[0]
  if (/(?::443)$/.test(authority) || /(?::80)$/.test(authority)) reject('default_port')
  if (url.hostname.endsWith('.') || url.href !== rawValue) reject('noncanonical_url')

  const hostname = stripIpv6Brackets(url.hostname)
  const literalFamily = isIP(hostname)
  const ownedLoopback =
    options.allowOwnedLoopback === true && literalFamily > 0 && classifyAddress(hostname).unsafe
  if (url.protocol !== 'https:' && !(ownedLoopback && url.protocol === 'http:'))
    reject('https_required')
  if (literalFamily > 0 && classifyAddress(hostname).unsafe && !ownedLoopback)
    reject('unsafe_address')
  if (ownedLoopback && !['127.0.0.1', '::1'].includes(hostname)) reject('owned_loopback_only')

  const segments = url.pathname.split('/').filter(Boolean)
  if (
    segments.some((segment) => !SAFE_SEGMENT.test(segment) || segment === '.' || segment === '..')
  ) {
    reject('unsafe_path')
  }
  const basePath = segments.length > 0 ? `/${segments.join('/')}` : ''
  return Object.freeze({
    url,
    hostname,
    origin: url.origin,
    basePath,
    allowedPath: `${basePath}/chat/completions`,
    ownedLoopback,
  })
}

export function assertResolvedAddresses(addresses, allowOwnedLoopback) {
  if (!Array.isArray(addresses) || addresses.length === 0) reject('dns_empty')
  const classified = addresses.map(({ address, family }) => {
    const result = classifyAddress(address)
    if (family !== undefined && family !== result.family) reject('dns_family_mismatch')
    return result
  })
  const unsafe = classified.filter((item) => item.unsafe)
  if (
    unsafe.length > 0 &&
    !(allowOwnedLoopback && classified.every((item) => ['127.0.0.1', '::1'].includes(item.address)))
  ) {
    reject('unsafe_dns_result')
  }
  return Object.freeze(classified[0])
}

export function sameNetworkAddress(actual, pinned) {
  try {
    const left = classifyAddress(actual)
    return left.family === pinned.family && left.address === pinned.address
  } catch {
    return false
  }
}

export function createPinnedConnection(policy, pinned) {
  return (_requestOptions, callback) => {
    const port = Number(policy.url.port || (policy.url.protocol === 'https:' ? 443 : 80))
    const connectOptions = { host: pinned.address, port, family: pinned.family }
    const socket =
      policy.url.protocol === 'https:'
        ? tls.connect({
            ...connectOptions,
            servername: isIP(policy.hostname) === 0 ? policy.hostname : undefined,
            rejectUnauthorized: true,
          })
        : net.connect(connectOptions)
    const readyEvent = policy.url.protocol === 'https:' ? 'secureConnect' : 'connect'
    let completed = false
    const complete = (error) => {
      if (completed) return
      completed = true
      callback(error, error ? undefined : socket)
    }
    socket.once('error', complete)
    socket.once(readyEvent, () => {
      if (!socket.remoteAddress || !sameNetworkAddress(socket.remoteAddress, pinned)) {
        const error = new E2EEgressPolicyError('remote_address_mismatch')
        socket.destroy(error)
        complete(error)
      } else complete()
    })
  }
}

export function writeExclusiveJson(target, value) {
  const temporary = join(
    dirname(target),
    `.${basename(target)}.${randomBytes(16).toString('hex')}.tmp`,
  )
  const descriptor = openSync(temporary, 'wx', 0o600)
  try {
    try {
      writeFileSync(descriptor, `${JSON.stringify(value)}\n`, { encoding: 'utf8' })
      fsyncSync(descriptor)
    } finally {
      closeSync(descriptor)
    }
    linkSync(temporary, target)
  } finally {
    unlinkSync(temporary)
  }
}

export function parsePositiveInteger(value, fallback, name) {
  if (value === undefined) return fallback
  if (!/^\d+$/.test(String(value)) || Number(value) < 1 || !Number.isSafeInteger(Number(value))) {
    throw new E2EEgressPolicyError(`invalid_${name}`)
  }
  return Number(value)
}

export function stableReceipt(records) {
  return Object.freeze({
    records: [...records.entries()]
      .map(([serialized, count]) => Object.freeze({ ...JSON.parse(serialized), count }))
      .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right))),
  })
}
