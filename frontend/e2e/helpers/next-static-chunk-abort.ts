export type NextStaticChunkAbortInput = {
  readonly errorText: string
  readonly method: string
  readonly resourceType: string
  readonly isMainFrame: boolean
  readonly requestUrl: string
  readonly currentPageUrl: string
  readonly elapsedSinceMainFrameNavigationMs: number
}

export type RequestFrameAccess = {
  readonly serviceWorker: () => unknown
  readonly frame: () => unknown
}

const NEXT_STATIC_CHUNKS_PREFIX = '/_next/static/chunks/'

/**
 * Playwright does not expose a frame for service-worker requests and can also
 * reject frame access while navigation tears down a document. Fail closed so
 * requestfailed handling never hides an unrelated transport error.
 */
export function isRequestFromMainFrame(request: RequestFrameAccess, mainFrame: unknown): boolean {
  try {
    return request.serviceWorker() === null && request.frame() === mainFrame
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
