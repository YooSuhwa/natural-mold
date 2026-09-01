export type NextStaticChunkAbortInput = {
  readonly errorText: string
  readonly method: string
  readonly resourceType: string
  readonly isMainFrame: boolean
  readonly requestUrl: string
  readonly currentPageUrl: string
  readonly elapsedSinceMainFrameNavigationMs: number
}

export type RequestStartAccess = {
  readonly isNavigationRequest: () => boolean
  readonly serviceWorker: () => unknown
  readonly frame: () => unknown
}

const NEXT_STATIC_CHUNKS_PREFIX = '/_next/static/chunks/'

/**
 * Record main-frame request identity while its Playwright accessors are still
 * safe. Service workers and iframe requests are never tracked; if request-start
 * frame access fails, the request remains visible to the E2E error collector.
 */
export function observeMainFrameRequestAtStart<TRequest extends RequestStartAccess>(
  observedRequests: WeakSet<TRequest>,
  request: TRequest,
  mainFrame: unknown,
): boolean {
  try {
    if (request.serviceWorker() !== null || request.frame() !== mainFrame) return false
    const isMainFrameNavigation = request.isNavigationRequest()
    observedRequests.add(request)
    return isMainFrameNavigation
  } catch {
    return false
  }
}

function isSameOriginNextJavaScriptChunk(requestUrl: string, currentPageUrl: string): boolean {
  try {
    const request = new URL(requestUrl)
    const currentPage = new URL(currentPageUrl)
    return (
      request.origin === currentPage.origin &&
      request.pathname.startsWith(NEXT_STATIC_CHUNKS_PREFIX) &&
      request.pathname.endsWith('.js')
    )
  } catch {
    return false
  }
}

/**
 * A main-frame reload can intentionally cancel a generated Next JavaScript chunk
 * that was in flight from the previous document. This accepts only that exact
 * transport artifact; HTTP errors still reach the response listener.
 */
export function isExpectedNextStaticChunkAbort(input: NextStaticChunkAbortInput): boolean {
  return (
    input.errorText === 'net::ERR_ABORTED' &&
    input.method === 'GET' &&
    input.resourceType === 'script' &&
    input.isMainFrame &&
    input.elapsedSinceMainFrameNavigationMs >= 0 &&
    input.elapsedSinceMainFrameNavigationMs < 1_000 &&
    isSameOriginNextJavaScriptChunk(input.requestUrl, input.currentPageUrl)
  )
}
