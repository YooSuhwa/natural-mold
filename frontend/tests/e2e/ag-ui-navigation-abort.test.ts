import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'

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
  await fixture.errors({ page }, async (errors) => run(page, () => errors.network), {
    annotations: [],
  })
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
