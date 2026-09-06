import { describe, expect, it } from 'vitest'

import {
  consumeMainFrameRequestFailure,
  isExpectedNextStaticChunkAbort,
  observeAndRecordMainFrameRequestAtStart,
  type NextStaticChunkAbortInput,
  type RequestStartAccess,
} from '../e2e/helpers/next-static-chunk-abort'

const validAbort: NextStaticChunkAbortInput = {
  errorText: 'net::ERR_ABORTED',
  method: 'GET',
  resourceType: 'script',
  isMainFrame: true,
  startedBeforeCurrentMainFrameNavigation: true,
  requestUrl: 'http://localhost:3100/_next/static/chunks/app/page.js?cache=1',
  currentPageUrl: 'http://localhost:3100/agents/agent-1/conversations/conversation-1',
}

function mainFrameRequest(isNavigationRequest: boolean, mainFrame: object): RequestStartAccess {
  return {
    isNavigationRequest: () => isNavigationRequest,
    serviceWorker: () => null,
    frame: () => mainFrame,
  }
}

describe('Next static chunk abort provenance', () => {
  it('accepts a delayed previous-document chunk after a later reload and cleans up its identity', () => {
    // Given: initial navigation -> old chunk -> reload navigation.
    const mainFrame = {}
    const requestGenerations = new WeakMap<RequestStartAccess, number>()
    let generation = 0
    const initialNavigation = mainFrameRequest(true, mainFrame)
    const oldChunk = mainFrameRequest(false, mainFrame)
    const reloadNavigation = mainFrameRequest(true, mainFrame)

    const initial = observeAndRecordMainFrameRequestAtStart(
      requestGenerations,
      initialNavigation,
      mainFrame,
      generation,
    )
    expect(initial).toEqual({ navigationGeneration: 1, mainFrameNavigationBegan: true })
    if (initial === undefined) return
    generation = initial.navigationGeneration
    const chunk = observeAndRecordMainFrameRequestAtStart(
      requestGenerations,
      oldChunk,
      mainFrame,
      generation,
    )
    expect(chunk).toEqual({ navigationGeneration: 1, mainFrameNavigationBegan: false })
    const reload = observeAndRecordMainFrameRequestAtStart(
      requestGenerations,
      reloadNavigation,
      mainFrame,
      generation,
    )
    expect(reload).toEqual({ navigationGeneration: 2, mainFrameNavigationBegan: true })
    if (reload === undefined) return
    generation = reload.navigationGeneration

    // When: the old chunk fails long after the reload began.
    const provenance = consumeMainFrameRequestFailure(requestGenerations, oldChunk, generation)
    const actual = isExpectedNextStaticChunkAbort({ ...validAbort, ...provenance })

    // Then: only the previous-generation identity is accepted and removed.
    expect(actual).toBe(true)
    expect(requestGenerations.has(oldChunk)).toBe(false)
  })

  it('rejects current-generation and equal-shaped unobserved identities', () => {
    // Given: a current chunk tracked after navigation and a different untracked request.
    const mainFrame = {}
    const requestGenerations = new WeakMap<RequestStartAccess, number>()
    const navigation = mainFrameRequest(true, mainFrame)
    const currentChunk = mainFrameRequest(false, mainFrame)
    const sameShapedUnobservedChunk = mainFrameRequest(false, mainFrame)
    const startedNavigation = observeAndRecordMainFrameRequestAtStart(
      requestGenerations,
      navigation,
      mainFrame,
      0,
    )
    expect(startedNavigation).toBeDefined()
    if (startedNavigation === undefined) return
    const generation = startedNavigation.navigationGeneration
    observeAndRecordMainFrameRequestAtStart(requestGenerations, currentChunk, mainFrame, generation)

    // When: each identity is consumed at failure time.
    const current = consumeMainFrameRequestFailure(requestGenerations, currentChunk, generation)
    const unobserved = consumeMainFrameRequestFailure(
      requestGenerations,
      sameShapedUnobservedChunk,
      generation,
    )

    // Then: neither identity has prior-generation provenance.
    expect(isExpectedNextStaticChunkAbort({ ...validAbort, ...current })).toBe(false)
    expect(isExpectedNextStaticChunkAbort({ ...validAbort, ...unobserved })).toBe(false)
    expect(requestGenerations.has(currentChunk)).toBe(false)
  })

  it('does not change the map or generation when request-start access is unsafe', () => {
    // Given: service-worker, frame, and navigation accessors that are unsafe or not page-main-frame.
    const mainFrame = {}
    const requestGenerations = new WeakMap<RequestStartAccess, number>()
    const generation = 7
    let serviceWorkerFrameCalls = 0
    const serviceWorkerThrow: RequestStartAccess = {
      isNavigationRequest: () => true,
      serviceWorker: () => {
        throw new Error('service worker unavailable')
      },
      frame: () => mainFrame,
    }
    const serviceWorkerRequest: RequestStartAccess = {
      isNavigationRequest: () => true,
      serviceWorker: () => ({}),
      frame: () => {
        serviceWorkerFrameCalls += 1
        return mainFrame
      },
    }
    const frameThrow: RequestStartAccess = {
      isNavigationRequest: () => true,
      serviceWorker: () => null,
      frame: () => {
        throw new Error('frame unavailable')
      },
    }
    const navigationThrow: RequestStartAccess = {
      isNavigationRequest: () => {
        throw new Error('navigation unavailable')
      },
      serviceWorker: () => null,
      frame: () => mainFrame,
    }
    const iframeRequest: RequestStartAccess = {
      isNavigationRequest: () => true,
      serviceWorker: () => null,
      frame: () => ({}),
    }

    // When: the shared fixture helper observes each request start.
    for (const request of [
      serviceWorkerThrow,
      serviceWorkerRequest,
      frameThrow,
      navigationThrow,
      iframeRequest,
    ]) {
      expect(
        observeAndRecordMainFrameRequestAtStart(requestGenerations, request, mainFrame, generation),
      ).toBeUndefined()
      expect(requestGenerations.has(request)).toBe(false)
    }

    // Then: no unsafe request can mutate provenance or the caller's generation.
    expect(generation).toBe(7)
    expect(serviceWorkerFrameCalls).toBe(0)
  })

  it.each([
    ['a connection reset', { errorText: 'net::ERR_CONNECTION_RESET' }],
    ['an abort with extra text', { errorText: 'net::ERR_ABORTED extra' }],
    ['a POST request', { method: 'POST' }],
    ['an image resource', { resourceType: 'image' }],
    ['an iframe request', { isMainFrame: false }],
    ['a current-navigation request', { startedBeforeCurrentMainFrameNavigation: false }],
    ['a cross-origin URL', { requestUrl: 'http://localhost:3200/_next/static/chunks/app/page.js' }],
    ['a Next image URL', { requestUrl: 'http://localhost:3100/_next/image?url=%2Flogo.png' }],
    ['an image path', { requestUrl: 'http://localhost:3100/_next/static/chunks/logo.png' }],
    ['a path outside chunks', { requestUrl: 'http://localhost:3100/_next/static/other/page.js' }],
    [
      'a chunks-prefix spoof',
      { requestUrl: 'http://localhost:3100/_next/static/chunks-evil/page.js' },
    ],
    ['a source map', { requestUrl: 'http://localhost:3100/_next/static/chunks/app/page.js.map' }],
    ['a stylesheet path', { requestUrl: 'http://localhost:3100/_next/static/chunks/app/page.css' }],
    [
      'a query-only JavaScript spoof',
      { requestUrl: 'http://localhost:3100/_next/static/chunks/?asset=app/page.js' },
    ],
    ['a malformed request URL', { requestUrl: 'not-a-url' }],
    ['a malformed page URL', { currentPageUrl: 'not-a-url' }],
  ])('rejects %s', (_label, overrides: Partial<NextStaticChunkAbortInput>) => {
    // Given: one exact abort invariant is violated.
    const input: NextStaticChunkAbortInput = { ...validAbort, ...overrides }

    // When: the failure is classified.
    const actual = isExpectedNextStaticChunkAbort(input)

    // Then: it remains visible to the error collector.
    expect(actual).toBe(false)
  })
})
