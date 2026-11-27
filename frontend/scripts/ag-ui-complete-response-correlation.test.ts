import { EventEmitter } from 'node:events'
import { setImmediate as nextTurn } from 'node:timers/promises'
import type { Page, Request } from '@playwright/test'
import { describe, expect, it, vi } from 'vitest'

import { installCompletedAgUiResponseObserver } from '../e2e/helpers/ag-ui-complete-response'

const apiBase = 'http://127.0.0.1:8101'
const identity = { threadId: 'thread-first', runId: 'public-first', physicalRunId: 'worker-first' }
const firstDocument = 'document-first'
type Signal = {
  kind: 'document' | 'closed' | 'eof' | 'invalid' | 'cancelled-terminal' | 'cancelled-eof'
  document?: string
  secret?: string
  threadId?: string
  runId?: string
  physicalRunId?: string
}
type Source = { page: object; frame: object }
type Receiver = (source: Source, value: unknown) => unknown

/** Deliver each independent Playwright-side event in a controlled, explicit order. */
async function setup() {
  const frame = {}
  const exposeBinding = vi
    .fn<(name: string, receiver: Receiver) => Promise<void>>()
    .mockResolvedValue(undefined)
  const addInitScript = vi
    .fn<(fn: unknown, config: { secret: string }) => Promise<void>>()
    .mockResolvedValue(undefined)
  const page = Object.assign(new EventEmitter(), {
    mainFrame: () => frame,
    exposeBinding,
    addInitScript,
  })
  const observer = await installCompletedAgUiResponseObserver(page as unknown as Page, apiBase)
  const receive = exposeBinding.mock.calls[0][1]
  const { secret } = addInitScript.mock.calls[0][1]
  function signal(value: Signal, source: Source = { page, frame }) {
    receive(source, { secret, document: firstDocument, ...value })
  }
  function request(
    options: {
      method?: string
      resourceType?: string
      headers?: Record<string, string>
      body?: string | null
      url?: string
      frame?: object
      redirected?: boolean
      serviceWorker?: boolean
      threadId?: string
      runId?: string
    } = {},
  ): Request {
    const input = {
      protocolVersion: '1.0',
      threadId: options.threadId ?? identity.threadId,
      runId: options.runId ?? identity.runId,
      state: {},
      messages: [],
      tools: [],
      context: [],
      forwardedProps: {},
    }
    return {
      frame: () => options.frame ?? frame,
      serviceWorker: () => (options.serviceWorker ? {} : null),
      redirectedFrom: () => (options.redirected ? {} : null),
      method: () => options.method ?? 'POST',
      resourceType: () => options.resourceType ?? 'fetch',
      url: () => options.url ?? apiBase + '/api/conversations/' + identity.threadId + '/ag-ui/runs',
      headers: () => options.headers ?? {},
      postData: () => (options.body === undefined ? JSON.stringify(input) : options.body),
    } as unknown as Request
  }
  function response(
    req: Request,
    overrides: {
      status?: number
      headers?: Record<string, string>
      fromServiceWorker?: boolean
      url?: string
      json?: () => Promise<unknown>
    } = {},
  ) {
    const headers = overrides.headers ?? {
      'content-type': 'text/event-stream; charset=utf-8',
      'x-run-id': identity.runId,
      'x-moldy-run-id': identity.physicalRunId,
    }
    page.emit('response', {
      request: () => req,
      url: () => overrides.url ?? req.url(),
      json: overrides.json,
      status: () => overrides.status ?? 200,
      headers: () => headers,
      fromServiceWorker: () => overrides.fromServiceWorker ?? false,
    })
  }
  function openCommand() {
    const run = request()
    page.emit('request', run)
    response(run)
    return run
  }
  function stop(options: Parameters<typeof request>[0] = {}) {
    return request({
      url:
        apiBase +
        '/api/conversations/' +
        identity.threadId +
        '/runs/' +
        identity.physicalRunId +
        '/cancel',
      headers: { 'x-csrf-token': 'test-only-token' },
      body: null,
      ...options,
    })
  }
  function stopResponse(req: Request, overrides: Parameters<typeof response>[1] = {}) {
    response(req, {
      headers: { 'content-type': 'application/json' },
      json: async () => ({
        id: identity.physicalRunId,
        conversation_id: identity.threadId,
        status: 'canceling',
      }),
      ...overrides,
    })
  }
  function canceledStream() {
    signal({ kind: 'cancelled-terminal', ...identity })
    signal({ kind: 'cancelled-eof', ...identity })
  }
  signal({ kind: 'document' })
  return {
    page,
    frame,
    signal,
    request,
    response,
    openCommand,
    stop,
    stopResponse,
    canceledStream,
    ...observer,
  }
}

describe('AG-UI native EOF evidence for an individual Playwright request', () => {
  it('requires the accepted request, its physical receipt and completion in the same document', async () => {
    const h = await setup()
    const accepted = h.request()
    const lookalike = h.request()
    h.page.emit('request', accepted)
    h.response(accepted)
    expect(h.hasCompleted(accepted)).toBe(false)
    h.signal({ kind: 'eof', ...identity })
    expect(h.hasCompleted(accepted)).toBe(true)
    expect(h.hasCompleted(lookalike)).toBe(false)
  })

  it('joins browser EOF to a later Playwright receipt only after that request has started', async () => {
    const h = await setup()
    const req = h.request()
    h.page.emit('request', req)
    h.signal({ kind: 'eof', ...identity })
    expect(h.hasCompleted(req)).toBe(false)
    h.response(req)
    expect(h.hasCompleted(req)).toBe(true)
  })

  it.each(['threadId', 'runId', 'physicalRunId'] as const)(
    'never transfers proof with a foreign %s',
    async (field) => {
      const h = await setup()
      const req = h.request()
      h.page.emit('request', req)
      h.response(req)
      h.signal({ kind: 'eof', ...identity, [field]: 'another-value' })
      expect(h.hasCompleted(req)).toBe(false)
    },
  )

  it('does not let another page, frame or binding report completion', async () => {
    const h = await setup()
    const req = h.request()
    h.page.emit('request', req)
    h.response(req)
    h.signal({ kind: 'eof', ...identity }, { page: {}, frame: h.frame })
    h.signal({ kind: 'eof', ...identity }, { page: h.page, frame: {} })
    h.signal({ kind: 'eof', ...identity, secret: 'different-binding' })
    expect(h.hasCompleted(req)).toBe(false)
  })

  it('leaves an outstanding request failed when a different run has already reached EOF', async () => {
    const h = await setup()
    const first = h.request()
    h.page.emit('request', first)
    h.response(first)
    h.signal({ kind: 'eof', ...identity })
    const second = h.request({ runId: 'public-second' })
    h.page.emit('request', second)
    h.response(second, {
      headers: {
        'content-type': 'text/event-stream',
        'x-run-id': 'public-second',
        'x-moldy-run-id': 'worker-second',
      },
    })
    expect(h.hasCompleted(first)).toBe(true)
    expect(h.hasCompleted(second)).toBe(false)
  })

  it('keeps the same public ID independent across two conversations in the same document', async () => {
    const h = await setup()
    const first = h.request()
    const threadId = 'thread-second'
    const second = h.request({
      threadId,
      url: apiBase + '/api/conversations/' + threadId + '/ag-ui/runs',
    })
    h.page.emit('request', first)
    h.page.emit('request', second)
    h.response(first)
    h.response(second)
    h.signal({ kind: 'eof', ...identity, threadId })
    expect(h.hasCompleted(first)).toBe(false)
    expect(h.hasCompleted(second)).toBe(true)
  })

  it('rejects both same-document requests when a public run ID is retried', async () => {
    const h = await setup()
    const first = h.request()
    h.page.emit('request', first)
    h.response(first)
    h.signal({ kind: 'eof', ...identity })
    expect(h.hasCompleted(first)).toBe(true)
    const retry = h.request()
    h.page.emit('request', retry)
    h.response(retry)
    h.signal({ kind: 'eof', ...identity })
    expect(h.hasCompleted(first)).toBe(false)
    expect(h.hasCompleted(retry)).toBe(false)
  })

  it('rejects delayed evidence from the old document on a fresh request with reused IDs', async () => {
    const h = await setup()
    const first = h.request()
    h.page.emit('request', first)
    h.response(first)
    h.signal({ kind: 'closed' })
    h.signal({ kind: 'document', document: 'document-second' })
    const second = h.request()
    h.page.emit('request', second)
    h.response(second)
    h.signal({ kind: 'eof', ...identity })
    expect(h.hasCompleted(first)).toBe(false)
    expect(h.hasCompleted(second)).toBe(false)
    h.signal({ kind: 'eof', ...identity, document: 'document-second' })
    expect(h.hasCompleted(second)).toBe(true)
  })

  it('retains previously confirmed evidence for its original request after navigation', async () => {
    const h = await setup()
    const req = h.request()
    h.page.emit('request', req)
    h.response(req)
    h.signal({ kind: 'eof', ...identity })
    h.signal({ kind: 'closed' })
    h.signal({ kind: 'document', document: 'document-second' })
    expect(h.hasCompleted(req)).toBe(true)
  })

  it('never grants future requests proof that arrived before their request event', async () => {
    const h = await setup()
    h.signal({ kind: 'eof', ...identity })
    const req = h.request()
    h.page.emit('request', req)
    h.response(req)
    expect(h.hasCompleted(req)).toBe(false)
  })

  it('retains invalidation after a premature end or canceled read on the same request', async () => {
    const h = await setup()
    const req = h.request()
    h.page.emit('request', req)
    h.response(req)
    h.signal({ kind: 'invalid', ...identity })
    h.signal({ kind: 'eof', ...identity })
    expect(h.hasCompleted(req)).toBe(false)
  })

  it('revokes previously confirmed EOF on invalidation for the same physical receipt', async () => {
    const h = await setup()
    const req = h.request()
    h.page.emit('request', req)
    h.response(req)
    h.signal({ kind: 'eof', ...identity })
    expect(h.hasCompleted(req)).toBe(true)
    h.signal({ kind: 'invalid', ...identity })
    expect(h.hasCompleted(req)).toBe(false)
  })

  it.each([
    ['GET replay', { method: 'GET' }],
    ['XHR', { resourceType: 'xhr' }],
    ['subframe', { frame: {} }],
    ['worker request', { serviceWorker: true }],
    ['redirected request', { redirected: true }],
    ['different command thread', { threadId: 'wrong-thread' }],
    ['other API', { url: 'http://127.0.0.1:9999/api/conversations/thread-first/ag-ui/runs' }],
  ] as const)('ignores a matching callback for a %s', async (_label, options) => {
    const h = await setup()
    const req = h.request(options)
    h.page.emit('request', req)
    h.response(req)
    h.signal({ kind: 'eof', ...identity })
    expect(h.hasCompleted(req)).toBe(false)
  })

  it.each([
    ['503', { status: 503 }],
    ['service worker', { fromServiceWorker: true }],
    [
      'JSON receipt',
      {
        headers: {
          'content-type': 'application/json',
          'x-run-id': identity.runId,
          'x-moldy-run-id': identity.physicalRunId,
        },
      },
    ],
    [
      'foreign receipt',
      {
        headers: {
          'content-type': 'text/event-stream',
          'x-run-id': 'public-other',
          'x-moldy-run-id': identity.physicalRunId,
        },
      },
    ],
    [
      'unclaimed input',
      {
        headers: {
          'content-type': 'text/event-stream',
          'x-run-id': identity.runId,
          'x-moldy-input-id': 'input-1',
          'x-moldy-input-status': 'pending',
          'x-moldy-input-position': '1',
          'x-moldy-input-revision': '1',
        },
      },
    ],
  ] as const)(
    'never accepts native proof without an executable receipt (%s)',
    async (_label, overrides) => {
      const h = await setup()
      const req = h.request()
      h.page.emit('request', req)
      h.response(req, overrides)
      h.signal({ kind: 'eof', ...identity })
      expect(h.hasCompleted(req)).toBe(false)
    },
  )
})

describe('AG-UI cancelled EOF after a scoped Stop acknowledgement', () => {
  it.each(['canceling', 'canceled'])(
    'joins %s JSON read after terminal and EOF to the earlier HTTP 200',
    async (status) => {
      const h = await setup()
      const run = h.openCommand()
      const stop = h.stop()
      h.page.emit('request', stop)
      let finishBody!: (value: unknown) => void
      const body = new Promise<unknown>((resolve) => {
        finishBody = resolve
      })
      h.stopResponse(stop, { json: () => body })
      h.canceledStream()
      expect(h.hasCompleted(run)).toBe(false)
      finishBody({ id: identity.physicalRunId, conversation_id: identity.threadId, status })
      await nextTurn()
      expect(h.hasCompleted(run)).toBe(true)
      expect(h.hasCompleted(stop)).toBe(false)
    },
  )

  it('requires an earlier response and both native cancellation signals', async () => {
    const h = await setup()
    const run = h.openCommand()
    const stop = h.stop()
    h.page.emit('request', stop)
    h.stopResponse(stop)
    await nextTurn()
    expect(h.hasCompleted(run)).toBe(false)
    h.signal({ kind: 'cancelled-terminal', ...identity })
    expect(h.hasCompleted(run)).toBe(false)
    h.signal({ kind: 'cancelled-eof', ...identity })
    expect(h.hasCompleted(run)).toBe(true)
  })

  it('keeps the cancelled terminal unconfirmed without any Stop request', async () => {
    const h = await setup()
    const run = h.openCommand()
    h.canceledStream()
    expect(h.hasCompleted(run)).toBe(false)
  })

  it('does not accept a cancelled EOF without its terminal marker even after a confirmed Stop', async () => {
    const h = await setup()
    const run = h.openCommand()
    const stop = h.stop()
    h.page.emit('request', stop)
    h.stopResponse(stop)
    await nextTurn()
    h.signal({ kind: 'cancelled-eof', ...identity })
    expect(h.hasCompleted(run)).toBe(false)
  })

  it('does not allow a Stop whose headers arrived after the cancellation terminal', async () => {
    const h = await setup()
    const run = h.openCommand()
    const stop = h.stop()
    h.page.emit('request', stop)
    h.signal({ kind: 'cancelled-terminal', ...identity })
    h.stopResponse(stop)
    await nextTurn()
    h.signal({ kind: 'cancelled-eof', ...identity })
    expect(h.hasCompleted(run)).toBe(false)
  })

  it('never substitutes an earlier ordinary EOF for the cancelled EOF', async () => {
    const h = await setup()
    const run = h.openCommand()
    const stop = h.stop()
    h.page.emit('request', stop)
    h.stopResponse(stop)
    await nextTurn()
    h.signal({ kind: 'eof', ...identity })
    expect(h.hasCompleted(run)).toBe(true)
    h.signal({ kind: 'cancelled-terminal', ...identity })
    expect(h.hasCompleted(run)).toBe(false)
  })

  it('rejects an ordinary EOF after a verified cancellation terminal', async () => {
    const h = await setup()
    const run = h.openCommand()
    const stop = h.stop()
    h.page.emit('request', stop)
    h.stopResponse(stop)
    await nextTurn()
    h.signal({ kind: 'cancelled-terminal', ...identity })
    h.signal({ kind: 'eof', ...identity })
    expect(h.hasCompleted(run)).toBe(false)
  })

  it('ignores a Stop body from a document closed before its confirmation arrived', async () => {
    const h = await setup()
    const run = h.openCommand()
    const stop = h.stop()
    h.page.emit('request', stop)
    let finishBody!: (value: unknown) => void
    const body = new Promise<unknown>((resolve) => {
      finishBody = resolve
    })
    h.stopResponse(stop, { json: () => body })
    h.canceledStream()
    h.signal({ kind: 'closed' })
    h.signal({ kind: 'document', document: 'document-second' })
    finishBody({
      id: identity.physicalRunId,
      conversation_id: identity.threadId,
      status: 'canceled',
    })
    await nextTurn()
    expect(h.hasCompleted(run)).toBe(false)
  })

  it('requires an unambiguous open command when two public IDs share the same physical receipt', async () => {
    const h = await setup()
    const first = h.openCommand()
    const secondPublicId = 'public-second'
    const second = h.request({ runId: secondPublicId })
    h.page.emit('request', second)
    h.response(second, {
      headers: {
        'content-type': 'text/event-stream',
        'x-run-id': secondPublicId,
        'x-moldy-run-id': identity.physicalRunId,
      },
    })
    const stop = h.stop()
    h.page.emit('request', stop)
    h.stopResponse(stop)
    await nextTurn()
    h.canceledStream()
    h.signal({ kind: 'cancelled-terminal', ...identity, runId: secondPublicId })
    h.signal({ kind: 'cancelled-eof', ...identity, runId: secondPublicId })
    expect(h.hasCompleted(first)).toBe(false)
    expect(h.hasCompleted(second)).toBe(false)
  })

  it('requires a response belonging to the same observed Stop request', async () => {
    const h = await setup()
    const run = h.openCommand()
    h.page.emit('request', h.stop())
    h.stopResponse(h.stop())
    await nextTurn()
    h.canceledStream()
    expect(h.hasCompleted(run)).toBe(false)
  })

  it.each([
    ['GET', { method: 'GET' }],
    ['missing CSRF', { headers: {} }],
    ['blank CSRF', { headers: { 'x-csrf-token': ' ' } }],
    ['nonempty body', { body: '{}' }],
    ['service worker', { serviceWorker: true }],
    ['subframe', { frame: {} }],
    ['redirect', { redirected: true }],
    ['XHR', { resourceType: 'xhr' }],
    [
      'foreign origin',
      { url: 'http://127.0.0.1:9101/api/conversations/thread-first/runs/worker-first/cancel' },
    ],
    [
      'foreign thread',
      { url: apiBase + '/api/conversations/thread-other/runs/worker-first/cancel' },
    ],
    [
      'foreign worker',
      { url: apiBase + '/api/conversations/thread-first/runs/worker-other/cancel' },
    ],
    [
      'independent job scope',
      {
        url:
          apiBase +
          '/api/conversations/thread-first/runs/worker-first/cancel?cancel_independent_jobs=true',
      },
    ],
  ] as const)('rejects a Stop request with %s', async (_label, options) => {
    const h = await setup()
    const run = h.openCommand()
    const stop = h.stop(options)
    h.page.emit('request', stop)
    h.stopResponse(stop)
    await nextTurn()
    h.canceledStream()
    expect(h.hasCompleted(run)).toBe(false)
  })

  it.each([
    ['unauthenticated', { status: 401 }],
    ['service worker', { fromServiceWorker: true }],
    ['wrong MIME', { headers: { 'content-type': 'text/event-stream' } }],
    ['wrong response URL', { url: apiBase + '/api/other' }],
    [
      'unfinished JSON',
      {
        json: async () => {
          throw new TypeError('body failed')
        },
      },
    ],
    [
      'unconfirmed status',
      {
        json: async () => ({
          id: identity.physicalRunId,
          conversation_id: identity.threadId,
          status: 'completed',
        }),
      },
    ],
    [
      'wrong worker',
      {
        json: async () => ({
          id: 'worker-other',
          conversation_id: identity.threadId,
          status: 'canceled',
        }),
      },
    ],
    [
      'wrong conversation',
      {
        json: async () => ({
          id: identity.physicalRunId,
          conversation_id: 'thread-other',
          status: 'canceled',
        }),
      },
    ],
  ] as const)('rejects %s in the Stop acknowledgement', async (_label, overrides) => {
    const h = await setup()
    const run = h.openCommand()
    const stop = h.stop()
    h.page.emit('request', stop)
    h.stopResponse(stop, overrides)
    await nextTurn()
    h.canceledStream()
    expect(h.hasCompleted(run)).toBe(false)
  })

  it('never lends the first worker’s Stop acknowledgement to a second command in the same conversation', async () => {
    const h = await setup()
    const first = h.openCommand()
    const secondIds = {
      threadId: identity.threadId,
      runId: 'public-second',
      physicalRunId: 'worker-second',
    }
    const second = h.request({ runId: secondIds.runId })
    h.page.emit('request', second)
    h.response(second, {
      headers: {
        'content-type': 'text/event-stream',
        'x-run-id': secondIds.runId,
        'x-moldy-run-id': secondIds.physicalRunId,
      },
    })
    const stop = h.stop()
    h.page.emit('request', stop)
    h.stopResponse(stop)
    await nextTurn()
    h.canceledStream()
    h.signal({ kind: 'cancelled-terminal', ...secondIds })
    h.signal({ kind: 'cancelled-eof', ...secondIds })
    expect(h.hasCompleted(first)).toBe(true)
    expect(h.hasCompleted(second)).toBe(false)
  })
})
