import { describe, expect, it } from 'vitest'

import {
  isExpectedNextStaticChunkAbort,
  isRequestFromMainFrame,
  type NextStaticChunkAbortInput,
  type RequestFrameAccess,
} from '../e2e/helpers/next-static-chunk-abort'

const validAbort: NextStaticChunkAbortInput = {
  errorText: 'net::ERR_ABORTED',
  method: 'GET',
  resourceType: 'script',
  isMainFrame: true,
  requestUrl: 'http://localhost:3100/_next/static/chunks/app/page.js?cache=1',
  currentPageUrl: 'http://localhost:3100/agents/agent-1/conversations/conversation-1',
  elapsedSinceMainFrameNavigationMs: 999,
}

describe('isExpectedNextStaticChunkAbort', () => {
  it('rejects a service-worker request without reading its frame', () => {
    // Given: a service-worker request whose frame accessor would fail if invoked.
    let frameCalls = 0
    const request: RequestFrameAccess = {
      serviceWorker: () => ({}),
      frame: () => {
        frameCalls += 1
        throw new Error('frame must not be read for a service worker')
      },
    }

    // When: main-frame identity is determined for the failed request.
    const actual = isRequestFromMainFrame(request, {})

    // Then: it fails closed before accessing the unavailable frame.
    expect(actual).toBe(false)
    expect(frameCalls).toBe(0)
  })

  it('rejects a request when its frame accessor throws', () => {
    // Given: a non-service-worker request whose frame is unavailable during navigation.
    const request: RequestFrameAccess = {
      serviceWorker: () => null,
      frame: () => {
        throw new Error('frame unavailable')
      },
    }

    // When: main-frame identity is determined.
    const actual = isRequestFromMainFrame(request, {})

    // Then: the classifier fails closed instead of failing the E2E fixture itself.
    expect(actual).toBe(false)
  })

  it('accepts only the actual main frame identity', () => {
    // Given: two distinct frame identities and ordinary document requests.
    const mainFrame = {}
    const otherFrame = {}
    const mainFrameRequest: RequestFrameAccess = {
      serviceWorker: () => null,
      frame: () => mainFrame,
    }
    const otherFrameRequest: RequestFrameAccess = {
      serviceWorker: () => null,
      frame: () => otherFrame,
    }

    // When: each request is compared with the page main frame.
    const mainFrameActual = isRequestFromMainFrame(mainFrameRequest, mainFrame)
    const otherFrameActual = isRequestFromMainFrame(otherFrameRequest, mainFrame)

    // Then: only object identity with the main frame is accepted.
    expect(mainFrameActual).toBe(true)
    expect(otherFrameActual).toBe(false)
  })

  it('accepts only the generated Next JavaScript chunk abort during main-frame navigation', () => {
    // Given: an exact same-origin main-frame generated chunk request that the browser aborts on reload.

    // When: the request failure is classified.
    const actual = isExpectedNextStaticChunkAbort(validAbort)

    // Then: the expected transport artifact is ignored by the E2E error collector.
    expect(actual).toBe(true)
    expect(
      isExpectedNextStaticChunkAbort({ ...validAbort, elapsedSinceMainFrameNavigationMs: 0 }),
    ).toBe(true)
  })

  it.each([
    ['a non-abort error', { errorText: 'net::ERR_FAILED' }],
    ['a connection reset', { errorText: 'net::ERR_CONNECTION_RESET' }],
    ['an abort with additional failure text', { errorText: 'net::ERR_ABORTED extra' }],
    ['a POST request', { method: 'POST' }],
    ['a stylesheet request', { resourceType: 'stylesheet' }],
    ['an image request', { resourceType: 'image' }],
    ['an image asset path', { requestUrl: 'http://localhost:3100/_next/static/chunks/logo.png' }],
    ['the Next image route', { requestUrl: 'http://localhost:3100/_next/image?url=%2Flogo.png' }],
    ['an iframe request', { isMainFrame: false }],
    [
      'a cross-origin request',
      { requestUrl: 'http://localhost:3200/_next/static/chunks/app/page.js' },
    ],
    [
      'a path outside static chunks',
      { requestUrl: 'http://localhost:3100/_next/static/other/page.js' },
    ],
    [
      'a static chunk path prefix spoof',
      { requestUrl: 'http://localhost:3100/_next/static/chunks-evil/page.js' },
    ],
    ['a source map', { requestUrl: 'http://localhost:3100/_next/static/chunks/app/page.js.map' }],
    ['a stylesheet path', { requestUrl: 'http://localhost:3100/_next/static/chunks/app/page.css' }],
    [
      'a query-only JavaScript path spoof',
      {
        requestUrl: 'http://localhost:3100/_next/static/chunks/?asset=app/page.js',
      },
    ],
    ['a malformed request URL', { requestUrl: 'not-a-url' }],
    ['a malformed current page URL', { currentPageUrl: 'not-a-url' }],
    ['a request before navigation starts', { elapsedSinceMainFrameNavigationMs: -1 }],
    ['the 1000ms boundary', { elapsedSinceMainFrameNavigationMs: 1000 }],
  ])('rejects %s', (_label, overrides: Partial<NextStaticChunkAbortInput>) => {
    // Given: one safety invariant differs from the exact approved tuple.
    const input: NextStaticChunkAbortInput = { ...validAbort, ...overrides }

    // When: the request failure is classified.
    const actual = isExpectedNextStaticChunkAbort(input)

    // Then: it remains visible to the E2E error collector.
    expect(actual).toBe(false)
  })
})
