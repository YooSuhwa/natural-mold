import { describe, expect, it } from 'vitest'

import {
  isExpectedNextStaticChunkAbort,
  observeMainFrameRequestAtStart,
  type NextStaticChunkAbortInput,
  type RequestStartAccess,
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
  it('tracks the same request identity before teardown without failed-time accessors', () => {
    // Given: a main-frame request whose browser accessors become unavailable after navigation starts.
    const mainFrame = {}
    const observedRequests = new WeakSet<RequestStartAccess>()
    let serviceWorkerCalls = 0
    let frameCalls = 0
    let isTornDown = false
    const request: RequestStartAccess = {
      isNavigationRequest: () => true,
      serviceWorker: () => {
        serviceWorkerCalls += 1
        if (isTornDown) throw new Error('service worker unavailable after teardown')
        return null
      },
      frame: () => {
        frameCalls += 1
        if (isTornDown) throw new Error('frame unavailable after teardown')
        return mainFrame
      },
    }

    // When: the request is observed at start and later fails after teardown.
    const startsMainFrameNavigation = observeMainFrameRequestAtStart(
      observedRequests,
      request,
      mainFrame,
    )
    isTornDown = true
    const remainsTrackedAtFailure = observedRequests.has(request)

    // Then: identity remains available without calling serviceWorker() or frame() again.
    expect(startsMainFrameNavigation).toBe(true)
    expect(remainsTrackedAtFailure).toBe(true)
    expect(serviceWorkerCalls).toBe(1)
    expect(frameCalls).toBe(1)
  })

  it('does not treat a different same-shaped request as observed', () => {
    // Given: two equal-looking requests with distinct identities.
    const mainFrame = {}
    const observedRequests = new WeakSet<RequestStartAccess>()
    const observedRequest: RequestStartAccess = {
      isNavigationRequest: () => false,
      serviceWorker: () => null,
      frame: () => mainFrame,
    }
    const sameShapedRequest: RequestStartAccess = {
      isNavigationRequest: () => false,
      serviceWorker: () => null,
      frame: () => mainFrame,
    }

    // When: only the first request is observed at start.
    observeMainFrameRequestAtStart(observedRequests, observedRequest, mainFrame)

    // Then: the failure-time lookup accepts only the exact request instance.
    expect(observedRequests.has(observedRequest)).toBe(true)
    expect(observedRequests.has(sameShapedRequest)).toBe(false)
  })

  it('rejects iframe, service-worker, and throwing request-start accessors before tracking', () => {
    // Given: requests that cannot be safely tied to the page main frame.
    const mainFrame = {}
    const otherFrame = {}
    const observedRequests = new WeakSet<RequestStartAccess>()
    let serviceWorkerFrameCalls = 0
    const iframeRequest: RequestStartAccess = {
      isNavigationRequest: () => true,
      serviceWorker: () => null,
      frame: () => otherFrame,
    }
    const serviceWorkerRequest: RequestStartAccess = {
      isNavigationRequest: () => true,
      serviceWorker: () => ({}),
      frame: () => {
        serviceWorkerFrameCalls += 1
        return mainFrame
      },
    }
    const frameThrowRequest: RequestStartAccess = {
      isNavigationRequest: () => true,
      serviceWorker: () => null,
      frame: () => {
        throw new Error('frame unavailable at request start')
      },
    }
    const navigationThrowRequest: RequestStartAccess = {
      isNavigationRequest: () => {
        throw new Error('navigation status unavailable at request start')
      },
      serviceWorker: () => null,
      frame: () => mainFrame,
    }

    // When: each request is observed at start.
    const iframeStartsNavigation = observeMainFrameRequestAtStart(
      observedRequests,
      iframeRequest,
      mainFrame,
    )
    const serviceWorkerStartsNavigation = observeMainFrameRequestAtStart(
      observedRequests,
      serviceWorkerRequest,
      mainFrame,
    )
    const frameThrowStartsNavigation = observeMainFrameRequestAtStart(
      observedRequests,
      frameThrowRequest,
      mainFrame,
    )
    const navigationThrowStartsNavigation = observeMainFrameRequestAtStart(
      observedRequests,
      navigationThrowRequest,
      mainFrame,
    )

    // Then: all fail closed, leave no unproven identity, and service workers never call frame().
    expect(iframeStartsNavigation).toBe(false)
    expect(serviceWorkerStartsNavigation).toBe(false)
    expect(frameThrowStartsNavigation).toBe(false)
    expect(navigationThrowStartsNavigation).toBe(false)
    expect(observedRequests.has(iframeRequest)).toBe(false)
    expect(observedRequests.has(serviceWorkerRequest)).toBe(false)
    expect(observedRequests.has(frameThrowRequest)).toBe(false)
    expect(observedRequests.has(navigationThrowRequest)).toBe(false)
    expect(serviceWorkerFrameCalls).toBe(0)
  })

  it('supports failure-time lookup cleanup and keeps the classifier identity-bound', () => {
    // Given: an observed main-frame script request and an unobserved same-shaped request.
    const mainFrame = {}
    const observedRequests = new WeakSet<RequestStartAccess>()
    const trackedRequest: RequestStartAccess = {
      isNavigationRequest: () => false,
      serviceWorker: () => null,
      frame: () => mainFrame,
    }
    const unobservedRequest: RequestStartAccess = {
      isNavigationRequest: () => false,
      serviceWorker: () => null,
      frame: () => mainFrame,
    }
    observeMainFrameRequestAtStart(observedRequests, trackedRequest, mainFrame)

    // When: failure-time code reads identity, runs the classifier, then deletes the request.
    const trackedAtFailure = observedRequests.has(trackedRequest)
    const trackedClassifierResult = isExpectedNextStaticChunkAbort({
      ...validAbort,
      isMainFrame: trackedAtFailure,
    })
    observedRequests.delete(trackedRequest)
    const unobservedClassifierResult = isExpectedNextStaticChunkAbort({
      ...validAbort,
      isMainFrame: observedRequests.has(unobservedRequest),
    })

    // Then: only tracked identity enables the exact classifier and cleanup removes it.
    expect(trackedClassifierResult).toBe(true)
    expect(unobservedClassifierResult).toBe(false)
    expect(observedRequests.has(trackedRequest)).toBe(false)
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
