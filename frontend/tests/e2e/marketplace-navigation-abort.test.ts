import { describe, expect, it } from 'vitest'
import {
  collectNetworkFailureCodes,
  deferredApiReadAbortCode,
  isApiReadNavigationAbortCandidate,
  isExpectedApiReadNavigationAbort,
  shouldDeferApiReadAbort,
  type DeferredApiReadAbort,
  type RequestFailureDiagnosticInput,
} from '../../e2e/helpers/network-failure-diagnostic'

const API = 'http://localhost:8101'
const input: RequestFailureDiagnosticInput = {
  requestUrl: `${API}/api/marketplace/versions/6e9a1380-40f1-4233-96db-ab7840b15af8`,
  currentPageUrl: 'http://localhost:3100/settings/marketplace-admin',
  method: 'GET',
  resourceType: 'fetch',
  errorText: 'net::ERR_ABORTED',
  isMainFrame: true,
  startedBeforeCurrentMainFrameNavigation: true,
}

describe('marketplace version read canceled by document navigation', () => {
  it('accepts the observed previous-document metadata cancellation', () => {
    expect(isExpectedApiReadNavigationAbort(input, API)).toBe(true)
  })

  it.each(['/api/agents', '/api/skills', '/api/skills/skill-id', '/api/agents?offset=0'])(
    'accepts a canceled prior-document API read: %s',
    (path) => {
      expect(isExpectedApiReadNavigationAbort({ ...input, requestUrl: `${API}${path}` }, API)).toBe(
        true,
      )
    },
  )

  it.each<Partial<RequestFailureDiagnosticInput>>([
    { startedBeforeCurrentMainFrameNavigation: false },
    { isMainFrame: false },
    { method: 'POST' },
    { resourceType: 'xhr' },
    { errorText: 'net::ERR_CONNECTION_RESET' },
    { requestUrl: input.requestUrl.replace('8101', '8201') },
    { requestUrl: `${API}/marketplace/items` },
    { requestUrl: `${API}/api` },
    { requestUrl: `${API}/apis/skills` },
    { requestUrl: 'not-a-url' },
  ])('retains every failure outside the exact transport and provenance contract: %j', (changed) => {
    expect(isExpectedApiReadNavigationAbort({ ...input, ...changed }, API)).toBe(false)
  })
})

describe('run status read canceled by document navigation', () => {
  const run = {
    ...input,
    requestUrl: `${API}/api/conversations/55ce328b-e4c5-49c2-a453-231e0b904b6b/runs/ec965b82-294b-4ed6-a2fe-15e0a30f6edb`,
  }

  it('accepts the observed previous-document run status cancellation', () => {
    expect(isExpectedApiReadNavigationAbort(run, API)).toBe(true)
  })

  it('rejects non-API paths even when the transport matches', () => {
    expect(isExpectedApiReadNavigationAbort({ ...run, requestUrl: `${API}/health` }, API)).toBe(
      false,
    )
  })

  it.each<Partial<RequestFailureDiagnosticInput>>([
    { startedBeforeCurrentMainFrameNavigation: false },
    { isMainFrame: false },
    { method: 'POST' },
    { resourceType: 'xhr' },
    { errorText: 'net::ERR_CONNECTION_RESET' },
    { requestUrl: run.requestUrl.replace('8101', '8201') },
    { requestUrl: run.requestUrl.replace('http://localhost:8101/api/', 'http://localhost:8101/') },
  ])('retains every unrelated transport or provenance failure: %j', (changed) => {
    expect(isExpectedApiReadNavigationAbort({ ...run, ...changed }, API)).toBe(false)
  })
})

describe('event-order provenance correction', () => {
  const deferred: DeferredApiReadAbort = {
    input: {
      ...input,
      requestUrl: `${API}/api/conversations/55ce328b-e4c5-49c2-a453-231e0b904b6b/runs/ec965b82-294b-4ed6-a2fe-15e0a30f6edb`,
      startedBeforeCurrentMainFrameNavigation: false,
    },
    startNavigationGeneration: 4,
  }

  it('reports before navigation and clears only after a later navigation', () => {
    expect(isApiReadNavigationAbortCandidate(deferred.input, API)).toBe(true)
    expect(deferredApiReadAbortCode(deferred, 4)).toBe('api_request_abort')
    expect(deferredApiReadAbortCode(deferred, 5)).toBeUndefined()
  })

  it('retains the candidate when no later navigation occurs', () => {
    expect(collectNetworkFailureCodes([], [deferred], 4)).toEqual(['api_request_abort'])
  })

  it('keeps a current-document candidate alongside a cleared prior candidate', () => {
    const current: DeferredApiReadAbort = { ...deferred, startNavigationGeneration: 5 }
    expect(collectNetworkFailureCodes([], [deferred, current], 5)).toEqual(['api_request_abort'])
  })

  it('retains unrelated request and response failures while clearing the candidate', () => {
    expect(
      collectNetworkFailureCodes(['api_request_abort', 'api_response_failure'], [deferred], 5),
    ).toEqual(['api_request_abort', 'api_response_failure'])
  })

  it('retains all codes when a current abort and response failure coexist', () => {
    const current: DeferredApiReadAbort = { ...deferred, startNavigationGeneration: 5 }
    expect(collectNetworkFailureCodes(['api_response_failure'], [current], 5)).toEqual([
      'api_request_abort',
      'api_response_failure',
    ])
  })

  it('does not defer an abort already covered by a benign stream rule', () => {
    expect(shouldDeferApiReadAbort(deferred.input, API, true)).toBe(false)
  })

  it('rejects current-document provenance at the helper boundary', () => {
    expect(
      isExpectedApiReadNavigationAbort(
        { ...deferred.input, startedBeforeCurrentMainFrameNavigation: false },
        API,
      ),
    ).toBe(false)
  })

  it('rejects a non-fetch API transport at the helper boundary', () => {
    expect(isApiReadNavigationAbortCandidate({ ...deferred.input, resourceType: 'xhr' }, API)).toBe(
      false,
    )
  })
})
