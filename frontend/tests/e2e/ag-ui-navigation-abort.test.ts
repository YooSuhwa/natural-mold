import { EventEmitter } from 'node:events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { API_BASE } from '../../e2e/fixtures'

type ErrorFixture = (
  fixtures: { page: EventEmitter & { mainFrame(): object; url(): string } },
  use: (errors: { network: string[] }) => Promise<void>,
  info: { annotations: unknown[] },
) => Promise<void>

const fixture = vi.hoisted(() => ({ errors: undefined as ErrorFixture | undefined }))
vi.mock('@playwright/test', () => ({
  test: { extend: (definition: { errors: ErrorFixture }) => Object.assign(fixture, definition) },
  expect,
}))

// The native reader has its own stream/EOF tests. This double records which exact
// browser request received its complete response; the real fixture decides the verdict.
const nativeCompletion = vi.hoisted(() => new WeakSet<object>())
vi.mock('../../e2e/helpers/ag-ui-complete-response', () => ({
  installCompletedAgUiResponseObserver: async () => ({
    hasCompleted: (request: object) => nativeCompletion.has(request),
    dispose: () => {},
  }),
}))

const commandUrl = `${API_BASE}/api/conversations/thread-a/ag-ui/runs`
const pageUrl = 'http://localhost:3100/conversations/thread-a'
const mainFrame = {}
const body = JSON.stringify({ protocolVersion: '1.0', threadId: 'thread-a', runId: 'public-id' })

function fetchRun() {
  return {
    url: () => commandUrl,
    frame: () => mainFrame,
    serviceWorker: () => null,
    resourceType: () => 'fetch',
    method: () => 'POST',
    postData: () => body,
    redirectedFrom: (): object | null => null,
    isNavigationRequest: () => false,
    failure: () => ({ errorText: 'net::ERR_ABORTED' }),
  }
}

function responseFor(request: ReturnType<typeof fetchRun>) {
  return {
    request: () => request,
    url: request.url,
    status: () => 200,
    fromServiceWorker: () => false,
    headers: (): Record<string, string> => ({
      'content-type': 'text/event-stream; charset=utf-8',
      'x-run-id': 'public-id',
      'x-moldy-run-id': 'physical-id',
    }),
  }
}

const navigation = () => ({
  ...fetchRun(),
  url: () => pageUrl,
  method: () => 'GET',
  resourceType: () => 'document',
  isNavigationRequest: () => true,
})

async function observe(run: (page: EventEmitter, codes: () => string[]) => void) {
  if (!fixture.errors) throw new Error('Expected the real errors fixture.')
  const page = Object.assign(new EventEmitter(), { mainFrame: () => mainFrame, url: () => pageUrl })
  const annotations: { type: string; description?: string }[] = []
  await fixture.errors({ page }, async (errors) => run(page, () => errors.network), { annotations })
  return annotations
}

describe('accepted AG-UI command across a document replacement', () => {
  it.each(['before', 'after'])(
    'can receive the failure %s the replacement commit',
    async (order) => {
      await observe((page, codes) => {
        const request = fetchRun()
        page.emit('request', request)
        page.emit('response', responseFor(request))
        page.emit('request', navigation())
        if (order === 'after') page.emit('framenavigated', mainFrame)
        page.emit('requestfailed', request)
        if (order === 'before') {
          expect(codes()).toEqual(['api_request_abort'])
          page.emit('framenavigated', mainFrame)
        }
        expect(codes()).toEqual([])
      })
    },
  )

  it.each(['before-navigation', 'different-request', 'late-response', 'failed-navigation'])(
    'retains the abort for %s even after a later committed navigation',
    async (scenario) => {
      await observe((page, codes) => {
        const request = fetchRun()
        const nav = navigation()
        page.emit('request', request)
        if (scenario !== 'late-response') {
          const received = scenario === 'different-request' ? fetchRun() : request
          if (received !== request) page.emit('request', received)
          page.emit('response', responseFor(received))
        }
        if (scenario !== 'before-navigation') page.emit('request', nav)
        page.emit('requestfailed', request)
        if (scenario === 'late-response') page.emit('response', responseFor(request))
        if (scenario === 'failed-navigation') {
          page.emit('requestfailed', nav)
          page.emit('framenavigated', mainFrame)
        }
        page.emit('request', navigation())
        page.emit('framenavigated', mainFrame)
        expect(codes()).toContain('api_request_abort')
      })
    },
  )

  it('accepts a queued rather than a claimed command only with a complete queue receipt', async () => {
    await observe((page, codes) => {
      const request = fetchRun()
      const response = responseFor(request)
      response.headers = () => ({
        'content-type': 'text/event-stream',
        'x-run-id': 'public-id',
        'x-moldy-input-id': 'queued-input',
        'x-moldy-input-status': 'pending',
        'x-moldy-input-position': '4',
        'x-moldy-input-revision': '2',
      })
      page.emit('request', request)
      page.emit('response', response)
      page.emit('request', navigation())
      page.emit('requestfailed', request)
      page.emit('framenavigated', mainFrame)
      expect(codes()).toEqual([])
    })
  })

  type RejectedCapture = {
    name: string
    request?: Partial<ReturnType<typeof fetchRun>>
    response?: Partial<ReturnType<typeof responseFor>>
    headers?: Record<string, string>
    expected?: string[]
  }

  const rejected: RejectedCapture[] = [
    { name: 'iframe', request: { frame: () => ({}) } },
    { name: 'XHR', request: { resourceType: () => 'xhr' } },
    {
      name: 'other origin',
      request: { url: () => 'https://other.test/api/conversations/thread-a/ag-ui/runs' },
    },
    { name: 'other thread', request: { postData: () => body.replace('thread-a', 'thread-b') } },
    { name: 'other protocol', request: { postData: () => body.replace('1.0', '0.9') } },
    { name: 'invalid JSON', request: { postData: () => 'invalid' } },
    { name: 'redirect', request: { redirectedFrom: () => ({}) } },
    { name: 'other command', request: { method: () => 'PUT' } },
    { name: 'other path', request: { url: () => commandUrl + '/different' } },
    { name: 'other public ID', headers: { 'x-run-id': 'another-run' } },
    { name: 'missing public ID', headers: { 'x-run-id': '' } },
    { name: 'no durable receipt', headers: { 'x-moldy-run-id': '' } },
    {
      name: 'incomplete queue receipt',
      headers: { 'x-moldy-run-id': '', 'x-moldy-input-id': 'unproven' },
    },
    { name: 'JSON response', headers: { 'content-type': 'application/json' } },
    { name: 'service worker response', response: { fromServiceWorker: () => true } },
    {
      name: 'lost connection',
      request: { failure: () => ({ errorText: 'net::ERR_CONNECTION_CLOSED' }) },
      expected: ['api_request_failure'],
    },
    {
      name: 'HTTP failure',
      response: { status: () => 503 },
      expected: ['api_request_abort', 'api_response_failure'],
    },
  ]

  it.each(rejected)('preserves a $name failure', async (scenario) => {
    await observe((page, codes) => {
      const request = { ...fetchRun(), ...scenario.request }
      const response = { ...responseFor(request), ...scenario.response }
      const headers = { ...response.headers(), ...scenario.headers }
      page.emit('request', request)
      page.emit('response', { ...response, headers: () => headers })
      page.emit('request', navigation())
      page.emit('requestfailed', request)
      page.emit('framenavigated', mainFrame)
      expect(codes()).toEqual(scenario.expected ?? ['api_request_abort'])
    })
  })
})

describe('completed AG-UI command in one document', () => {
  beforeEach(() => {
    vi.stubEnv('NEXT_PUBLIC_CHAT_RUNTIME', 'ag_ui')
  })
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it.each(['before', 'after'] as const)(
    'checks native completion %s the abort callback',
    async (order) => {
      const annotations = await observe((page, codes) => {
        const request = fetchRun()
        page.emit('request', request)
        page.emit('response', responseFor(request))
        if (order === 'before') nativeCompletion.add(request)
        page.emit('requestfailed', request)
        if (order === 'after') {
          expect(codes()).toEqual(['api_request_abort'])
          nativeCompletion.add(request)
        }
        expect(codes()).toEqual([])
      })
      expect(annotations).toEqual([])
    },
  )

  it.each(['before', 'after'] as const)(
    'retains only a completion proved %s a later document commit',
    async (order) => {
      const annotations = await observe((page, codes) => {
        const request = fetchRun()
        page.emit('request', request)
        page.emit('response', responseFor(request))
        page.emit('requestfailed', request)
        expect(codes()).toEqual(['api_request_abort'])
        if (order === 'before') nativeCompletion.add(request)
        // No getter runs between completion and navigation in a real user flow.
        page.emit('request', navigation())
        page.emit('framenavigated', mainFrame)
        if (order === 'after') nativeCompletion.add(request)
        expect(codes()).toEqual(order === 'before' ? [] : ['api_request_abort'])
      })
      const failures = annotations.filter((item) => item.type === 'moldy.network-failure.v1')
      expect(failures.map((item) => item.description)).toEqual(
        order === 'before' ? [] : ['api_request_abort'],
      )
    },
  )

  it.each(['without-proof', 'another-request', 'late-receipt', 'unrelated-commit'] as const)(
    'retains an early abort with %s in the getter and final annotations',
    async (reason) => {
      const annotations = await observe((page, codes) => {
        const request = fetchRun()
        page.emit('request', request)
        if (reason !== 'late-receipt') page.emit('response', responseFor(request))
        page.emit('requestfailed', request)
        if (reason === 'another-request' || reason === 'unrelated-commit') {
          if (reason === 'unrelated-commit') {
            page.emit('request', navigation())
            page.emit('framenavigated', mainFrame)
          }
          const other = fetchRun()
          page.emit('request', other)
          page.emit('response', responseFor(other))
          nativeCompletion.add(other)
        } else if (reason === 'late-receipt') {
          page.emit('response', responseFor(request))
          nativeCompletion.add(request)
        }
        expect(codes()).toEqual(['api_request_abort'])
      })
      expect(annotations).toContainEqual({
        type: 'moldy.network-failure.v1',
        description: 'api_request_abort',
      })
      expect(
        annotations.filter((item) => item.type === 'moldy.api-request-failure.v1'),
      ).toHaveLength(1)
    },
  )

  it('keeps the abort when the client uses the LangGraph runtime', async () => {
    vi.stubEnv('NEXT_PUBLIC_CHAT_RUNTIME', 'langgraph_v3')
    const annotations = await observe((page, codes) => {
      const request = fetchRun()
      page.emit('request', request)
      page.emit('response', responseFor(request))
      nativeCompletion.add(request)
      page.emit('requestfailed', request)
      expect(codes()).toEqual(['api_request_abort'])
    })
    expect(annotations).toContainEqual({
      type: 'moldy.network-failure.v1',
      description: 'api_request_abort',
    })
  })

  it('keeps a concurrent interrupted command when the other command finished', async () => {
    const annotations = await observe((page, codes) => {
      const completed = fetchRun()
      const interrupted = {
        ...fetchRun(),
        postData: () => body.replace('public-id', 'second-public-id'),
      }
      page.emit('request', completed)
      page.emit('request', interrupted)
      page.emit('response', responseFor(completed))
      const response = responseFor(interrupted)
      page.emit('response', {
        ...response,
        headers: () => ({
          ...response.headers(),
          'x-run-id': 'second-public-id',
          'x-moldy-run-id': 'second-physical-id',
        }),
      })
      page.emit('requestfailed', interrupted)
      nativeCompletion.add(completed)
      page.emit('requestfailed', completed)
      expect(codes()).toEqual(['api_request_abort'])
    })
    expect(annotations.filter((item) => item.type === 'moldy.network-failure.v1')).toHaveLength(1)
    expect(annotations.filter((item) => item.type === 'moldy.api-request-failure.v1')).toHaveLength(
      1,
    )
  })

  it.each([
    {
      name: 'wrong command path',
      request: { url: () => `${commandUrl}/other-command` },
    },
    {
      name: 'snapshot GET',
      request: {
        url: () => `${API_BASE}/api/conversations/thread-a/ag-ui/snapshot`,
        method: () => 'GET',
      },
    },
    {
      name: 'different thread in body',
      request: { postData: () => body.replace('thread-a', 'thread-b') },
    },
    { name: 'missing public receipt', headers: { 'x-run-id': '' } },
    { name: 'wrong public receipt', headers: { 'x-run-id': 'other-run' } },
    {
      name: 'queue-only receipt',
      headers: {
        'x-moldy-run-id': '',
        'x-moldy-input-id': 'queued-input',
        'x-moldy-input-status': 'pending',
        'x-moldy-input-position': '1',
        'x-moldy-input-revision': '1',
      },
    },
    { name: 'other frame', request: { frame: () => ({}) } },
    { name: 'HTTP error', status: 500 },
  ])(
    'preserves a $name failure even if a reader finished',
    async ({ request: overrides, headers, status }) => {
      const annotations = await observe((page, codes) => {
        const request = { ...fetchRun(), ...overrides }
        const response = responseFor(request)
        page.emit('request', request)
        page.emit('response', {
          ...response,
          status: () => status ?? 200,
          headers: () => ({ ...response.headers(), ...headers }),
        })
        nativeCompletion.add(request)
        page.emit('requestfailed', request)
        expect(codes()).toContain('api_request_abort')
        if (status) expect(codes()).toContain('api_response_failure')
      })
      expect(annotations).toContainEqual({
        type: 'moldy.network-failure.v1',
        description: 'api_request_abort',
      })
    },
  )
})
