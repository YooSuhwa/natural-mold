import { describe, expect, it } from 'vitest'

import {
  NETWORK_FAILURE_ANNOTATION_TYPE,
  NETWORK_FAILURE_CODES,
  classifyRequestFailure,
  classifyResponseFailure,
  recordNetworkFailure,
  type NetworkFailureAnnotation,
  type NetworkFailureCode,
  type RequestFailureDiagnosticInput,
} from '../e2e/helpers/network-failure-diagnostic'

const nextChunkAbort: RequestFailureDiagnosticInput = {
  requestUrl:
    'http://localhost:3100/_next/static/chunks/app/secret-path.js?api_key=must-not-survive',
  currentPageUrl: 'http://localhost:3100/agents/current',
  errorText: 'net::ERR_ABORTED',
  method: 'GET',
  resourceType: 'script',
  isMainFrame: true,
  startedBeforeCurrentMainFrameNavigation: true,
}

describe('network failure diagnostics', () => {
  it.each([
    ['next_chunk_abort_prior_document', nextChunkAbort],
    [
      'next_chunk_abort_current_document',
      { ...nextChunkAbort, startedBeforeCurrentMainFrameNavigation: false },
    ],
    [
      'next_chunk_abort_unobserved',
      { ...nextChunkAbort, isMainFrame: false, startedBeforeCurrentMainFrameNavigation: false },
    ],
    [
      'api_request_abort',
      {
        ...nextChunkAbort,
        requestUrl: 'http://user:password@localhost:8101/api/private?token=must-not-survive',
        method: 'POST',
        resourceType: 'fetch',
      },
    ],
    [
      'api_request_failure',
      {
        ...nextChunkAbort,
        requestUrl: 'http://localhost:8101/api/private?token=must-not-survive',
        errorText: 'arbitrary raw browser failure must-not-survive',
        method: 'POST',
        resourceType: 'fetch',
      },
    ],
    [
      'other_request_abort',
      { ...nextChunkAbort, requestUrl: 'http://localhost:8101/assets/private', method: 'POST' },
    ],
    [
      'other_request_failure',
      {
        ...nextChunkAbort,
        requestUrl: 'not a URL with must-not-survive',
        errorText: 'net::ERR_ABORTED extra',
      },
    ],
  ] satisfies readonly [NetworkFailureCode, RequestFailureDiagnosticInput][])(
    'classifies %s without serializing raw request data',
    (expected, input) => {
      const actual = classifyRequestFailure(input)

      expect(actual).toBe(expected)
      expect(JSON.stringify(actual)).not.toContain('must-not-survive')
    },
  )

  it.each([
    ['api_response_failure', 'http://user:password@localhost:8101/api/private?token=secret'],
    ['other_response_failure', 'not a URL containing secret'],
  ] satisfies readonly [NetworkFailureCode, string][])(
    'classifies %s without serializing raw response data',
    (expected, requestUrl) => {
      const actual = classifyResponseFailure({ requestUrl })

      expect(actual).toBe(expected)
      expect(JSON.stringify(actual)).not.toContain('secret')
    },
  )

  it('requires the exact browser abort text', () => {
    expect(classifyRequestFailure({ ...nextChunkAbort, errorText: 'net::ERR_ABORTED extra' })).toBe(
      'other_request_failure',
    )
  })

  it('deduplicates, sorts, and bounds annotations by the finite code set', () => {
    const codes: NetworkFailureCode[] = []
    const annotations: NetworkFailureAnnotation[] = []

    for (const code of [...NETWORK_FAILURE_CODES].reverse()) {
      recordNetworkFailure(codes, annotations, code)
      recordNetworkFailure(codes, annotations, code)
    }

    expect(codes).toEqual([...NETWORK_FAILURE_CODES].sort())
    expect(codes).toHaveLength(NETWORK_FAILURE_CODES.length)
    expect(annotations).toHaveLength(NETWORK_FAILURE_CODES.length)
    expect(annotations.every(({ type }) => type === NETWORK_FAILURE_ANNOTATION_TYPE)).toBe(true)
    expect(annotations.map(({ description }) => description).sort()).toEqual(codes)
  })
})
