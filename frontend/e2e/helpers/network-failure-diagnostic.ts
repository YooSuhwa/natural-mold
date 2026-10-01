import {
  isNextStaticChunkRequestAbort,
  type MainFrameRequestFailure,
} from './next-static-chunk-abort'

export const NETWORK_FAILURE_ANNOTATION_TYPE = 'moldy.network-failure.v1'

export const NETWORK_FAILURE_CODES = [
  'api_request_abort',
  'api_request_failure',
  'api_response_failure',
  'next_chunk_abort_current_document',
  'next_chunk_abort_prior_document',
  'next_chunk_abort_unobserved',
  'other_request_abort',
  'other_request_failure',
  'other_response_failure',
] as const

export type NetworkFailureCode = (typeof NETWORK_FAILURE_CODES)[number]

export type NetworkFailureAnnotation = {
  readonly type: string
  readonly description?: string
}

export type RequestFailureDiagnosticInput = MainFrameRequestFailure & {
  readonly requestUrl: string
  readonly currentPageUrl: string
  readonly errorText: string
  readonly method: string
  readonly resourceType: string
}

export type ResponseFailureDiagnosticInput = {
  readonly requestUrl: string
}

type NextRscPrefetchAbortInput = Pick<
  RequestFailureDiagnosticInput,
  'currentPageUrl' | 'errorText' | 'method' | 'requestUrl' | 'resourceType'
>

function isApiUrl(value: string): boolean {
  try {
    const path = new URL(value).pathname
    return path === '/api' || path.startsWith('/api/')
  } catch {
    return false
  }
}

/** Ignore only canceled, same-origin Next.js RSC prefetches; response failures remain observable. */
export function isExpectedNextRscPrefetchAbort(input: NextRscPrefetchAbortInput): boolean {
  if (
    input.errorText !== 'net::ERR_ABORTED' ||
    input.method !== 'GET' ||
    input.resourceType !== 'fetch'
  ) {
    return false
  }
  try {
    const requestUrl = new URL(input.requestUrl)
    const currentPageUrl = new URL(input.currentPageUrl)
    return requestUrl.origin === currentPageUrl.origin && requestUrl.searchParams.has('_rsc')
  } catch {
    return false
  }
}

function nextChunkAbortCode(input: RequestFailureDiagnosticInput): NetworkFailureCode | undefined {
  if (!isNextStaticChunkRequestAbort(input)) return undefined
  if (!input.isMainFrame) return 'next_chunk_abort_unobserved'
  return input.startedBeforeCurrentMainFrameNavigation
    ? 'next_chunk_abort_prior_document'
    : 'next_chunk_abort_current_document'
}

/** A full document navigation can cancel the previous page's metadata reads. */
export function isExpectedApiReadNavigationAbort(
  input: RequestFailureDiagnosticInput,
  apiBaseUrl: string,
): boolean {
  if (
    input.errorText !== 'net::ERR_ABORTED' ||
    input.method !== 'GET' ||
    input.resourceType !== 'fetch' ||
    !input.isMainFrame ||
    !input.startedBeforeCurrentMainFrameNavigation
  )
    return false
  try {
    const request = new URL(input.requestUrl)
    if (request.origin !== new URL(apiBaseUrl).origin) return false
    const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    return (
      new RegExp(`^/api/marketplace/versions/${uuid}$`, 'i').test(request.pathname) ||
      new RegExp(`^/api/conversations/${uuid}/runs/${uuid}$`, 'i').test(request.pathname)
    )
  } catch {
    return false
  }
}

/** Recognize a strict prior-document API-read abort candidate independent of the
 * event-order provenance captured by Playwright. */
export function isApiReadNavigationAbortCandidate(
  input: RequestFailureDiagnosticInput,
  apiBaseUrl: string,
): boolean {
  return isExpectedApiReadNavigationAbort(
    { ...input, startedBeforeCurrentMainFrameNavigation: true },
    apiBaseUrl,
  )
}

export type DeferredApiReadAbort = {
  readonly input: RequestFailureDiagnosticInput
  readonly startNavigationGeneration: number
}

/** Return the candidate's effective code until a later main-frame navigation
 * proves that it belonged to the prior document. */
export function deferredApiReadAbortCode(
  candidate: DeferredApiReadAbort,
  currentNavigationGeneration: number,
): NetworkFailureCode | undefined {
  return currentNavigationGeneration > candidate.startNavigationGeneration
    ? undefined
    : classifyRequestFailure(candidate.input)
}

export function collectNetworkFailureCodes(
  persistentCodes: readonly NetworkFailureCode[],
  deferredCandidates: readonly DeferredApiReadAbort[],
  currentNavigationGeneration: number,
): NetworkFailureCode[] {
  const codes = [...persistentCodes]
  for (const candidate of deferredCandidates) {
    const code = deferredApiReadAbortCode(candidate, currentNavigationGeneration)
    if (code !== undefined && !codes.includes(code)) codes.push(code)
  }
  return codes.sort()
}

/** Reduce one failed browser request to a finite code without retaining raw inputs. */
export function classifyRequestFailure(input: RequestFailureDiagnosticInput): NetworkFailureCode {
  const nextChunkCode = nextChunkAbortCode(input)
  if (nextChunkCode !== undefined) return nextChunkCode
  const aborted = input.errorText === 'net::ERR_ABORTED'
  if (isApiUrl(input.requestUrl)) return aborted ? 'api_request_abort' : 'api_request_failure'
  return aborted ? 'other_request_abort' : 'other_request_failure'
}

/** Reduce one non-success browser response to a finite code without retaining raw inputs. */
export function classifyResponseFailure(input: ResponseFailureDiagnosticInput): NetworkFailureCode {
  return isApiUrl(input.requestUrl) ? 'api_response_failure' : 'other_response_failure'
}

/** Record each finite code once in deterministic order and annotate the Playwright result. */
export function recordNetworkFailure(
  codes: NetworkFailureCode[],
  annotations: NetworkFailureAnnotation[],
  code: NetworkFailureCode,
): void {
  if (codes.includes(code)) return
  codes.push(code)
  codes.sort()
  annotations.push({ type: NETWORK_FAILURE_ANNOTATION_TYPE, description: code })
}
