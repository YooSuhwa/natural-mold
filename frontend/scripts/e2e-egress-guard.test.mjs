// @vitest-environment node

import { createServer, request as requestHttp } from 'node:http'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtempSync, readFileSync, watch } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import { startE2EEgressGuard } from './e2e-egress-guard.mjs'
import { validateConfiguredBaseUrl } from './e2e-egress-policy.mjs'
import { server as mockServiceWorker } from '../tests/setup.ts'

const guards = []
const servers = []

beforeAll(() => mockServiceWorker.close())

async function listen(handler) {
  const server = createServer(handler)
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  servers.push(server)
  return `http://127.0.0.1:${server.address().port}`
}

async function startGuard(upstreamOrigin, options = {}) {
  const guard = await startE2EEgressGuard({
    configuredBaseUrl: `${upstreamOrigin}/v1`,
    upstreamApiKey: 'upstream-key-not-for-client',
    proxyToken: 'run-scoped-client-token-that-is-long-enough',
    allowOwnedLoopback: true,
    ...options,
  })
  guards.push(guard)
  return guard
}

async function guardedFetch(guard, suffix = '/chat/completions', init = {}) {
  return fetch(`${guard.proxyBaseUrl.slice(0, -3)}${suffix}`, {
    method: 'POST',
    body: '{}',
    ...init,
    headers: {
      authorization: `Bearer ${guard.proxyToken}`,
      'content-type': 'application/json',
      ...init.headers,
    },
    redirect: 'manual',
  })
}

afterEach(async () => {
  await Promise.all(guards.splice(0).map((guard) => guard.close()))
  await Promise.all(
    servers.splice(0).map((server) => new Promise((resolve) => server.close(resolve))),
  )
})

describe('live E2E egress guard', () => {
  it('pauses and resumes an upstream response for a slow downstream consumer', async () => {
    // Given: an upstream stream and a client that stops consuming after proxy response headers.
    const backpressure = []
    const upstream = await listen((_request, response) => {
      response.writeHead(200, { 'content-type': 'application/octet-stream' })
      for (let index = 0; index < 128; index += 1) response.write(Buffer.alloc(32_768, index))
      response.end()
    })
    let resolvePaused
    const paused = new Promise((resolve) => {
      resolvePaused = resolve
    })
    let resolveResumed
    const resumed = new Promise((resolve) => {
      resolveResumed = resolve
    })
    const guard = await startGuard(upstream, {
      onBackpressure: (event) => {
        backpressure.push(event)
        if (event === 'response_paused') resolvePaused()
        if (event === 'response_resumed') resolveResumed()
      },
    })

    // When: the downstream HTTP response is deliberately paused during a large upstream stream.
    const proxied = requestHttp(`${guard.proxyBaseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        authorization: `Bearer ${guard.proxyToken}`,
        'content-type': 'application/json',
      },
    })
    proxied.end('{}')
    const [response] = await once(proxied, 'response')
    let forwardedBytes = 0
    response.on('data', (chunk) => {
      forwardedBytes += chunk.length
    })
    response.pause()
    await Promise.race([
      paused,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('response was not paused')), 2_000),
      ),
    ])
    response.resume()
    await Promise.race([
      resumed,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('response was not resumed')), 2_000),
      ),
    ])
    await once(response, 'end')

    // Then: the proxy applies true reverse-direction flow control before resuming the upstream read.
    expect(backpressure).toContain('response_paused')
    expect(backpressure).toContain('response_resumed')
    expect(forwardedBytes).toBe(4_194_304)
  })

  it('pauses and resumes the client request for a slow upstream consumer', async () => {
    // Given: an upstream that does not consume its request body until the proxy has filled its write buffer.
    let receivedBytes = 0
    let resumeUpstream
    let resolveUpstreamReady
    const upstreamReady = new Promise((resolve) => {
      resolveUpstreamReady = resolve
    })
    const upstream = await listen((request, response) => {
      request.pause()
      resumeUpstream = () => request.resume()
      resolveUpstreamReady()
      request.on('data', (chunk) => {
        receivedBytes += chunk.length
      })
      request.on('end', () => response.end('{}'))
    })
    const backpressure = []
    let resolvePaused
    const paused = new Promise((resolve) => {
      resolvePaused = resolve
    })
    let resolveResumed
    const resumed = new Promise((resolve) => {
      resolveResumed = resolve
    })
    const guard = await startGuard(upstream, {
      limits: { requestBytes: 1_048_576 },
      onBackpressure: (event) => {
        backpressure.push(event)
        if (event === 'request_paused') resolvePaused()
        if (event === 'request_resumed') resolveResumed()
      },
    })

    // When: a large client body is forwarded to the deliberately slow upstream reader.
    const proxied = guardedFetch(guard, '/v1/chat/completions', {
      body: JSON.stringify({ prompt: 'x'.repeat(262_144) }),
    })
    await Promise.race([
      paused,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('request was not paused')), 2_000),
      ),
    ])
    await upstreamReady
    resumeUpstream()
    await Promise.race([
      resumed,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('request was not resumed')), 2_000),
      ),
    ])
    await (await proxied).text()

    // Then: ingress is paused until the upstream socket drains, then forwarding completes.
    expect(backpressure).toContain('request_paused')
    expect(backpressure).toContain('request_resumed')
    expect(receivedBytes).toBeGreaterThan(262_144)
  })

  it('enforces an absolute request deadline when an upstream slow-drips bytes', async () => {
    // Given: an upstream that keeps its socket active while exceeding the configured total request duration.
    const upstream = await listen((_request, response) => {
      response.writeHead(200, { 'content-type': 'text/event-stream' })
      let count = 0
      const interval = setInterval(() => {
        response.write(`data: ${count}\n\n`)
        count += 1
        if (count === 8) {
          clearInterval(interval)
          response.end()
        }
      }, 20)
    })
    const guard = await startGuard(upstream, { limits: { timeoutMs: 75 } })

    // When: the upstream continuously sends bytes for longer than the absolute deadline.
    const response = await guardedFetch(guard, '/v1/chat/completions')

    // Then: the downstream stream is terminated despite regular upstream socket activity.
    expect(response.status).toBe(200)
    await expect(response.text()).rejects.toThrow()
  })

  it('streams and forwards non-streaming inference only to the exact joined path', async () => {
    // Given: an owned fake upstream that records only test-observable request metadata.
    const observations = []
    const upstream = await listen((request, response) => {
      // prettier-ignore
      observations.push({ url: request.url, authorization: request.headers.authorization, accept: request.headers.accept, userAgent: request.headers['user-agent'], leaked: request.headers['x-external-key'] })
      if (observations.length === 1) {
        response.writeHead(200, { 'content-type': 'text/event-stream' })
        response.write('data: first\n\n')
        queueMicrotask(() => response.end('data: done\n\n'))
      } else {
        response.writeHead(200, { 'content-type': 'application/json' })
        response.end('{"mode":"ordinary"}')
      }
    })
    const guard = await startGuard(upstream)

    // When: streaming and ordinary JSON-shaped calls traverse the run-owned proxy.
    const streamed = await guardedFetch(guard, '/v1/chat/completions', {
      headers: { 'x-external-key': 'must-not-propagate' },
    })
    const ordinary = await guardedFetch(guard, '/v1/chat/completions')

    // Then: both use the exact /v1 route, receive streamed bytes, and only the proxy-owned key reaches upstream.
    expect(await streamed.text()).toBe('data: first\n\ndata: done\n\n')
    expect(await ordinary.json()).toEqual({ mode: 'ordinary' })
    // prettier-ignore
    expect(observations).toEqual([
      { url: '/v1/chat/completions', authorization: 'Bearer upstream-key-not-for-client', accept: 'application/json, text/event-stream', userAgent: 'moldy-e2e-egress/1.0', leaked: undefined },
      { url: '/v1/chat/completions', authorization: 'Bearer upstream-key-not-for-client', accept: 'application/json, text/event-stream', userAgent: 'moldy-e2e-egress/1.0', leaked: undefined },
    ])
  })

  it.each([
    ['GET', '/v1/models'],
    ['POST', '/v1/responses'],
    ['POST', '/v1/embeddings'],
    ['POST', '/v1/images/generations'],
    ['POST', '/v1/chat/completions?probe=1'],
    ['POST', '/v1%2fchat/completions'],
    ['POST', '/v1%5cchat/completions'],
    ['POST', '/v1/%2e%2e/chat/completions'],
    ['PUT', '/v1/chat/completions'],
    ['POST', '/v1/chat/completions', { origin: 'https://other-origin.test' }],
  ])('denies %s %s without contacting upstream', async (method, suffix, extraHeaders = {}) => {
    // Given: a fake upstream with a contact counter.
    let contacts = 0
    const upstream = await listen((_request, response) => {
      contacts += 1
      response.end('{}')
    })
    const guard = await startGuard(upstream)

    // When: a non-allowlisted method or path reaches the proxy.
    const response = await fetch(`${guard.proxyBaseUrl.slice(0, -3)}${suffix}`, {
      method,
      body: ['GET', 'HEAD'].includes(method) ? undefined : '{}',
      // prettier-ignore
      headers: { authorization: `Bearer ${guard.proxyToken}`, 'content-type': 'application/json', ...extraHeaders },
      redirect: 'manual',
    })

    // Then: it is denied locally and the external key is never used.
    expect(response.status).toBe(403)
    expect(contacts).toBe(0)
  })

  it('denies redirects instead of following a cross-host Location', async () => {
    // Given: an upstream redirect and a second server that must remain untouched.
    let pivotContacts = 0
    const pivot = await listen((_request, response) => {
      pivotContacts += 1
      response.end('pivot')
    })
    const upstream = await listen((_request, response) => {
      response.writeHead(302, { location: `${pivot}/capture` })
      response.end()
    })
    const guard = await startGuard(upstream)

    // When: the allowed inference endpoint returns a redirect.
    const response = await guardedFetch(guard, '/v1/chat/completions')

    // Then: the proxy converts it to a local failure without contacting the pivot.
    expect(response.status).toBe(502)
    expect(response.headers.get('location')).toBeNull()
    expect(pivotContacts).toBe(0)
  })

  it.each([
    'http://169.254.169.254/v1',
    'https://169.254.169.254/v1',
    'https://224.0.0.1/v1',
    'https://10.0.0.1/v1',
    'https://100.64.0.1/v1',
    'https://127.0.0.1/v1',
    'https://[::1]/v1',
    'https://[::ffff:127.0.0.1]/v1',
    'ftp://api.example.test/v1',
    'https://user@api.example.test/v1',
    'https://api.example.test:443/v1',
    'https://api.example.test/v1?query=1',
    'https://api.example.test/v1%2fadmin',
  ])('rejects unsafe or ambiguous configured upstream %s', (configuredBaseUrl) => {
    // Given / When / Then: unsafe configuration fails before any network connection.
    expect(() => validateConfiguredBaseUrl(configuredBaseUrl)).toThrow('egress policy rejected')
  })

  it('rejects a DNS answer that pivots an external origin to a private address', async () => {
    // Given: a canonical external HTTPS origin whose resolver returns metadata space.
    const dnsLookup = async () => [{ address: '169.254.169.254', family: 4 }]

    // When / Then: startup fails closed before creating a listener.
    // prettier-ignore
    await expect(startE2EEgressGuard({
      configuredBaseUrl: 'https://api.example.test/v1', upstreamApiKey: 'upstream-only-key',
      proxyToken: 'run-scoped-client-token-that-is-long-enough', dnsLookup,
    })).rejects.toThrow('egress policy rejected')
  })

  it('bounds bodies and produces a stable secret-free aggregate receipt', async () => {
    // Given: an upstream that emits a secret-looking header and response body.
    const upstream = await listen((_request, response) => {
      response.writeHead(200, {
        'content-type': 'application/json',
        'x-upstream-secret': 'response-secret',
      })
      response.end('{"content":"body-secret"}')
    })
    const guard = await startGuard(upstream, { limits: { requestBytes: 64 } })

    // When: one valid request and one oversized request are handled.
    const accepted = await guardedFetch(guard, '/v1/chat/completions', {
      body: '{"prompt":"request-body-secret"}',
    })
    expect(await accepted.text()).toContain('body-secret')
    const oversized = await guardedFetch(guard, '/v1/chat/completions', {
      body: JSON.stringify({ too: 'x'.repeat(100) }),
    })
    const firstReceipt = guard.receipt()

    // Then: headers are not relayed, limits fail locally, and recordings contain no body/header/key/token/path URL material.
    expect(accepted.headers.get('x-upstream-secret')).toBeNull()
    expect(oversized.status).toBe(413)
    expect(guard.receipt()).toEqual(firstReceipt)
    const serialized = JSON.stringify(firstReceipt)
    expect(serialized).not.toMatch(
      /body-secret|response-secret|upstream-key|client-token|chat\/completions|authorization|x-upstream/i,
    )
    expect(firstReceipt.records).toEqual([
      { method: 'POST', origin: upstream, path_class: 'chat_completions', status: 200, count: 1 },
      { method: 'POST', origin: upstream, path_class: 'chat_completions', status: 413, count: 1 },
    ])
  })

  it('terminates an upstream response that exceeds the configured response body cap', async () => {
    // Given: an upstream response larger than the guard's explicit response limit.
    const upstream = await listen((_request, response) => {
      response.writeHead(200, { 'content-type': 'application/json' })
      response.end('x'.repeat(128))
    })
    const guard = await startGuard(upstream, { limits: { responseBytes: 64 } })

    // When: the allowed inference route receives the oversized response.
    // Then: downstream cannot consume the over-limit body.
    await expect(guardedFetch(guard, '/v1/chat/completions')).rejects.toThrow()
  })

  it('rejects concurrent requests beyond the configured per-guard limit', async () => {
    // Given: an upstream that keeps the first accepted request active.
    let releaseFirst
    let resolveFirstStarted
    const firstStarted = new Promise((resolve) => {
      resolveFirstStarted = resolve
    })
    const upstream = await listen((request, response) => {
      request.resume()
      request.once('end', () => {
        resolveFirstStarted()
        releaseFirst = () => response.end('{}')
      })
    })
    const guard = await startGuard(upstream, { limits: { concurrency: 1 } })

    // When: a second request arrives while the first request remains active.
    const first = guardedFetch(guard, '/v1/chat/completions')
    await firstStarted
    const rejected = await guardedFetch(guard, '/v1/chat/completions')
    releaseFirst()
    await (await first).text()

    // Then: only the first request is admitted and the excess request is denied locally.
    expect(rejected.status).toBe(429)
  })

  it('closes held upstream requests when downstream clients disconnect before response headers', async () => {
    // Given: an upstream that holds each request open without emitting response headers.
    let activeUpstreams = 0
    let closedUpstreams = 0
    let maximumActiveUpstreams = 0
    let resolveOpened
    let resolveClosed
    const upstream = await listen((request) => {
      activeUpstreams += 1
      maximumActiveUpstreams = Math.max(maximumActiveUpstreams, activeUpstreams)
      resolveOpened()
      request.resume()
      request.socket.once('close', () => {
        activeUpstreams -= 1
        closedUpstreams += 1
        resolveClosed()
      })
    })
    const guard = await startGuard(upstream, { limits: { concurrency: 2 } })

    // When: downstream clients abandon more held requests than the guard's concurrency limit.
    for (let index = 0; index < 5; index += 1) {
      const opened = new Promise((resolve) => {
        resolveOpened = resolve
      })
      const closed = new Promise((resolve) => {
        resolveClosed = resolve
      })
      const client = requestHttp(`${guard.proxyBaseUrl}/chat/completions`, {
        method: 'POST',
        headers: {
          authorization: `Bearer ${guard.proxyToken}`,
          'content-type': 'application/json',
        },
      })
      client.on('error', () => {})
      client.end('{}')
      await opened
      client.destroy()
      await closed
    }

    // Then: every abandoned outbound connection closes and cannot accumulate past the concurrency cap.
    expect(closedUpstreams).toBe(5)
    expect(activeUpstreams).toBe(0)
    expect(maximumActiveUpstreams).toBeLessThanOrEqual(2)
  })

  it.each([
    ['SIGTERM', 143],
    ['SIGINT', 130],
  ])('runs as a silent CLI and handles %s without secrets', async (signal, expectedExitCode) => {
    // Given: a run-owned output directory and the documented CLI environment.
    const upstream = await listen((_request, response) => response.end('{}'))
    const runRoot = mkdtempSync(path.join(tmpdir(), 'moldy-egress-'))
    const readyFile = path.join(runRoot, 'ready.json')
    const receiptFile = path.join(runRoot, 'receipt.json')
    const readyCreated = new Promise((resolve, reject) => {
      const watcher = watch(runRoot, (event, filename) => {
        if (event === 'rename' && filename === 'ready.json') {
          watcher.close()
          resolve()
        }
      })
      watcher.once('error', reject)
    })
    const child = spawn(process.execPath, [path.resolve('scripts/e2e-egress-guard.mjs')], {
      cwd: path.resolve('.'),
      env: {
        ...process.env,
        E2E_EGRESS_UPSTREAM_BASE_URL: `${upstream}/v1`,
        E2E_EGRESS_UPSTREAM_API_KEY: 'cli-upstream-secret',
        E2E_EGRESS_PROXY_TOKEN: 'cli-run-scoped-token-that-is-long-enough',
        E2E_EGRESS_READY_FILE: readyFile,
        E2E_EGRESS_RECEIPT_FILE: receiptFile,
        E2E_EGRESS_ALLOW_OWNED_LOOPBACK: '1',
      },
      stdio: 'ignore',
    })

    // When: the CLI becomes ready, serves one request, and receives the termination signal.
    await Promise.race([
      readyCreated,
      new Promise((_, reject) => setTimeout(() => reject(new Error('ready timeout')), 5_000)),
    ])
    const ready = JSON.parse(readFileSync(readyFile, 'utf8'))
    const response = await fetch(`${ready.proxyBaseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        authorization: 'Bearer cli-run-scoped-token-that-is-long-enough',
        'content-type': 'application/json',
      },
      body: '{}',
    })
    await response.text()
    child.kill(signal)
    const [exitCode, exitSignal] = await once(child, 'exit')

    // Then: the signal is handled, stdout is unused, and both artifacts remain secret-free.
    expect({ exitCode, signal: exitSignal }).toEqual({ exitCode: expectedExitCode, signal: null })
    expect(Object.keys(ready).sort()).toEqual(['pid', 'proxyBaseUrl', 'ready'])
    const receipt = JSON.parse(readFileSync(receiptFile, 'utf8'))
    expect(Object.keys(receipt.records[0]).sort()).toEqual([
      'count',
      'method',
      'origin',
      'path_class',
      'status',
    ])
    expect(`${JSON.stringify(ready)}${JSON.stringify(receipt)}`).not.toMatch(
      /cli-upstream-secret|cli-run-scoped-token/i,
    )
  })
})
