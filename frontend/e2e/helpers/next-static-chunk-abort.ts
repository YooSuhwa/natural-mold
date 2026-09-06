export type NextStaticChunkAbortInput = {
  readonly errorText: string
  readonly method: string
  readonly resourceType: string
  readonly isMainFrame: boolean
  readonly startedBeforeCurrentMainFrameNavigation: boolean
  readonly requestUrl: string
  readonly currentPageUrl: string
}

export type RequestStartAccess = {
  readonly isNavigationRequest: () => boolean
  readonly serviceWorker: () => unknown
  readonly frame: () => unknown
}

export type MainFrameRequestStart = {
  readonly navigationGeneration: number
  readonly mainFrameNavigationBegan: boolean
}

export type MainFrameRequestFailure = {
  readonly isMainFrame: boolean
  readonly startedBeforeCurrentMainFrameNavigation: boolean
}

const NEXT_STATIC_CHUNKS_PREFIX = '/_next/static/chunks/'

/**
 * Safely observe and bind an exact page-main-frame request to its navigation
 * generation. Unsafe accessors leave both the map and caller generation intact.
 */
export function observeAndRecordMainFrameRequestAtStart<
  TRequest extends RequestStartAccess & object,
>(
  requestGenerations: WeakMap<TRequest, number>,
  request: TRequest,
  mainFrame: unknown,
  currentNavigationGeneration: number,
): MainFrameRequestStart | undefined {
  try {
    if (request.serviceWorker() !== null || request.frame() !== mainFrame) return undefined
    const mainFrameNavigationBegan = request.isNavigationRequest()
    let navigationGeneration = currentNavigationGeneration
    if (mainFrameNavigationBegan) navigationGeneration += 1
    requestGenerations.set(request, navigationGeneration)
    return { navigationGeneration, mainFrameNavigationBegan }
  } catch {
    return undefined
  }
}

/** Consume and remove the exact request provenance before failure classification. */
export function consumeMainFrameRequestFailure<TRequest extends object>(
  requestGenerations: WeakMap<TRequest, number>,
  request: TRequest,
  currentNavigationGeneration: number,
): MainFrameRequestFailure {
  const requestGeneration = requestGenerations.get(request)
  requestGenerations.delete(request)
  return {
    isMainFrame: requestGeneration !== undefined,
    startedBeforeCurrentMainFrameNavigation:
      requestGeneration !== undefined && requestGeneration < currentNavigationGeneration,
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

/** Match the exact browser transport shape before applying request provenance. */
export function isNextStaticChunkRequestAbort(
  input: Pick<
    NextStaticChunkAbortInput,
    'errorText' | 'method' | 'resourceType' | 'requestUrl' | 'currentPageUrl'
  >,
): boolean {
  return (
    input.errorText === 'net::ERR_ABORTED' &&
    input.method === 'GET' &&
    input.resourceType === 'script' &&
    isSameOriginNextJavaScriptChunk(input.requestUrl, input.currentPageUrl)
  )
}

/**
 * A main-frame reload can intentionally cancel a generated Next JavaScript chunk
 * that was in flight from the previous document. This accepts only that exact
 * transport artifact; HTTP errors still reach the response listener.
 */
export function isExpectedNextStaticChunkAbort(input: NextStaticChunkAbortInput): boolean {
  return (
    isNextStaticChunkRequestAbort(input) &&
    input.isMainFrame &&
    input.startedBeforeCurrentMainFrameNavigation
  )
}
