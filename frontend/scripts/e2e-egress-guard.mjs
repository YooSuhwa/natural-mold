#!/usr/bin/env node

import { randomBytes } from 'node:crypto'
import { lookup as systemLookup } from 'node:dns/promises'
import http from 'node:http'
import https from 'node:https'
import { fileURLToPath } from 'node:url'

import {
  assertResolvedAddresses,
  createPinnedConnection,
  E2EEgressPolicyError,
  stableReceipt,
  validateConfiguredBaseUrl,
} from './e2e-egress-policy.mjs'
import { runE2EEgressGuardCli } from './e2e-egress-guard-cli.mjs'

const DEFAULT_LIMITS = Object.freeze({
  requestBytes: 4_194_304,
  responseBytes: 33_554_432,
  timeoutMs: 30_000,
  concurrency: 2,
})

function recordAggregate(records, entry) {
  const key = JSON.stringify(entry)
  records.set(key, (records.get(key) ?? 0) + 1)
}

function classifyPath(requestUrl, allowedPath) {
  if (requestUrl === allowedPath) return 'chat_completions'
  const pathname = requestUrl.split('?', 1)[0]
  if (pathname.endsWith('/models')) return 'models_denied'
  if (/\/(?:responses|embeddings|images)(?:\/|$)/.test(pathname)) return 'capability_denied'
  return 'other_denied'
}

function safeResponseContentType(value) {
  if (typeof value !== 'string') return 'application/octet-stream'
  if (value.toLowerCase().startsWith('text/event-stream')) return 'text/event-stream'
  if (value.toLowerCase().startsWith('application/json')) return 'application/json'
  return 'application/octet-stream'
}

export async function startE2EEgressGuard(options) {
  const policy = validateConfiguredBaseUrl(options.configuredBaseUrl, {
    allowOwnedLoopback: options.allowOwnedLoopback === true,
  })
  if (typeof options.upstreamApiKey !== 'string' || options.upstreamApiKey.length === 0) {
    throw new E2EEgressPolicyError('missing_upstream_key')
  }
  const token = options.proxyToken ?? randomBytes(32).toString('base64url')
  if (typeof token !== 'string' || token.length < 32 || token === options.upstreamApiKey) {
    throw new E2EEgressPolicyError('invalid_proxy_token')
  }
  const limits = Object.freeze({ ...DEFAULT_LIMITS, ...options.limits })
  const resolved =
    policy.url.hostname === policy.hostname && /^[\d.]+$/.test(policy.hostname)
      ? [{ address: policy.hostname, family: 4 }]
      : policy.hostname === '::1'
        ? [{ address: '::1', family: 6 }]
        : await (options.dnsLookup ?? systemLookup)(policy.hostname, { all: true, verbatim: true })
  const pinned = assertResolvedAddresses(resolved, policy.ownedLoopback)
  const onBackpressure =
    typeof options.onBackpressure === 'function' ? options.onBackpressure : () => {}
  const records = new Map()
  const upstreamRequests = new Set()
  let active = 0
  let localAuthority = ''

  const server = http.createServer({ maxHeaderSize: 16_384 }, (request, response) => {
    const method = request.method ?? 'UNKNOWN'
    const pathClass = classifyPath(request.url ?? '', policy.allowedPath)
    let outcomeRecorded = false
    const finish = (status) => {
      if (response.headersSent || response.destroyed) return response.destroy()
      if (outcomeRecorded) return
      outcomeRecorded = true
      recordAggregate(records, { method, origin: policy.origin, path_class: pathClass, status })
      response.writeHead(status, {
        'content-type': 'application/json',
        'cache-control': 'no-store',
      })
      response.end('{"error":"egress_denied"}')
    }
    const wrongOrigin =
      request.headers.origin && request.headers.origin !== `http://${localAuthority}`
    if (
      request.headers.host !== localAuthority ||
      wrongOrigin ||
      request.url !== policy.allowedPath ||
      method !== 'POST'
    )
      return finish(403)
    if (request.headers.authorization !== `Bearer ${token}`) return finish(401)
    if (!/^application\/json(?:\s*;|$)/i.test(request.headers['content-type'] ?? ''))
      return finish(415)
    if (active >= limits.concurrency) return finish(429)
    const declaredLength = Number(request.headers['content-length'] ?? 0)
    if (
      !Number.isFinite(declaredLength) ||
      declaredLength < 0 ||
      declaredLength > limits.requestBytes
    )
      return finish(413)

    active += 1
    let released = false
    let requestBytes = 0
    let requestDeadline
    let upstream
    let upstreamResponse
    const release = () => {
      if (!released) active -= 1
      released = true
      clearTimeout(requestDeadline)
    }
    response.once('close', () => {
      upstreamResponse?.destroy()
      upstream?.destroy()
      release()
    })
    const client = policy.url.protocol === 'https:' ? https : http
    upstream = client.request({
      protocol: policy.url.protocol,
      hostname: policy.hostname,
      port: policy.url.port || undefined,
      method: 'POST',
      path: policy.allowedPath,
      createConnection: createPinnedConnection(policy, pinned),
      headers: {
        authorization: `Bearer ${options.upstreamApiKey}`,
        accept: 'application/json, text/event-stream',
        'content-type': 'application/json',
        'user-agent': 'moldy-e2e-egress/1.0',
      },
      timeout: limits.timeoutMs,
    })
    upstreamRequests.add(upstream)
    upstream.once('close', () => upstreamRequests.delete(upstream))
    upstream.once('timeout', () => upstream.destroy(new Error('upstream_timeout')))
    upstream.once('error', () => {
      if (!response.headersSent) finish(502)
      else response.destroy()
    })
    requestDeadline = setTimeout(() => {
      upstream.destroy(new Error('request_elapsed_timeout'))
      if (response.headersSent) response.destroy()
      else finish(504)
    }, limits.timeoutMs)
    requestDeadline.unref()
    upstream.once('response', (receivedResponse) => {
      upstreamResponse = receivedResponse
      const status = upstreamResponse.statusCode ?? 502
      if (status >= 300 && status < 400) {
        upstreamResponse.destroy()
        return finish(502)
      }
      outcomeRecorded = true
      recordAggregate(records, { method, origin: policy.origin, path_class: pathClass, status })
      response.writeHead(status, {
        'content-type': safeResponseContentType(upstreamResponse.headers['content-type']),
        'cache-control': 'no-store',
      })
      let responseBytes = 0
      upstreamResponse.on('data', (chunk) => {
        responseBytes += chunk.length
        if (responseBytes > limits.responseBytes) {
          upstreamResponse.destroy()
          response.destroy()
        } else if (!response.write(chunk)) {
          upstreamResponse.pause()
          onBackpressure('response_paused')
          response.once('drain', () => {
            if (response.destroyed || upstreamResponse.destroyed) return
            upstreamResponse.resume()
            onBackpressure('response_resumed')
          })
        }
      })
      upstreamResponse.once('end', () => response.end())
      upstreamResponse.once('error', () => response.destroy())
    })
    request.on('data', (chunk) => {
      requestBytes += chunk.length
      if (requestBytes > limits.requestBytes) {
        upstream.destroy()
        if (!response.headersSent) finish(413)
      } else if (!upstream.write(chunk)) {
        request.pause()
        onBackpressure('request_paused')
        upstream.once('drain', () => {
          if (request.destroyed || response.destroyed || upstream.destroyed) return
          request.resume()
          onBackpressure('request_resumed')
        })
      }
    })
    request.once('end', () => upstream.end())
    request.once('aborted', () => upstream.destroy())
  })
  server.requestTimeout = limits.timeoutMs
  server.headersTimeout = limits.timeoutMs
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(options.port ?? 0, '127.0.0.1', resolve)
  })
  const address = server.address()
  if (!address || typeof address === 'string') throw new E2EEgressPolicyError('listen_failed')
  localAuthority = `127.0.0.1:${address.port}`
  let closed = false
  return Object.freeze({
    proxyBaseUrl: `http://${localAuthority}${policy.basePath}`,
    proxyToken: token,
    receipt: () => stableReceipt(records),
    close: async () => {
      if (closed) return
      closed = true
      for (const upstream of upstreamRequests) upstream.destroy()
      const stopped = new Promise((resolve) => server.close(resolve))
      server.closeAllConnections()
      await stopped
    },
  })
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  runE2EEgressGuardCli(startE2EEgressGuard).catch(() => {
    process.exitCode = 1
  })
}
