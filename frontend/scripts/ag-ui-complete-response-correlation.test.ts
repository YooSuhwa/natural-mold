import { EventEmitter } from 'node:events'
import type { Page, Request } from '@playwright/test'
import { describe, expect, it, vi } from 'vitest'

import { installCompletedAgUiResponseObserver } from '../e2e/helpers/ag-ui-complete-response'

const apiBase = 'http://127.0.0.1:8101'
const identity = { threadId: 'thread-first', runId: 'public-first', physicalRunId: 'worker-first' }
const firstDocument = 'document-first'
type Signal = {
  kind: 'document' | 'closed' | 'eof' | 'invalid'
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
      postData: () => JSON.stringify(input),
    } as unknown as Request
  }
  function response(
    req: Request,
    overrides: {
      status?: number
      headers?: Record<string, string>
      fromServiceWorker?: boolean
    } = {},
  ) {
    const headers = overrides.headers ?? {
      'content-type': 'text/event-stream; charset=utf-8',
      'x-run-id': identity.runId,
      'x-moldy-run-id': identity.physicalRunId,
    }
    page.emit('response', {
      request: () => req,
      url: req.url,
      status: () => overrides.status ?? 200,
      headers: () => headers,
      fromServiceWorker: () => overrides.fromServiceWorker ?? false,
    })
  }
  signal({ kind: 'document' })
  return { page, frame, signal, request, response, ...observer }
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
