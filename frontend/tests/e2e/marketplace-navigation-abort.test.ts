import { describe, expect, it } from 'vitest'
import {
  isExpectedApiReadNavigationAbort,
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

  it.each<Partial<RequestFailureDiagnosticInput>>([
    { startedBeforeCurrentMainFrameNavigation: false },
    { isMainFrame: false },
    { method: 'POST' },
    { resourceType: 'xhr' },
    { errorText: 'net::ERR_CONNECTION_RESET' },
    { requestUrl: input.requestUrl.replace('8101', '8201') },
    { requestUrl: `${API}/api/marketplace/items` },
    { requestUrl: `${input.requestUrl}/install` },
    { requestUrl: `${API}/api/marketplace/versions/not-an-id` },
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

  it.each<Partial<RequestFailureDiagnosticInput>>([
    { startedBeforeCurrentMainFrameNavigation: false },
    { isMainFrame: false },
    { method: 'POST' },
    { resourceType: 'xhr' },
    { errorText: 'net::ERR_CONNECTION_RESET' },
    { requestUrl: run.requestUrl.replace('8101', '8201') },
    { requestUrl: `${run.requestUrl}/stream` },
    { requestUrl: run.requestUrl.replace(/runs\/[^/]+$/, 'runs') },
    { requestUrl: run.requestUrl.replace(/runs\/[^/]+$/, 'runs/not-an-id') },
    { requestUrl: run.requestUrl.replace('55ce328b-e4c5-49c2-a453-231e0b904b6b', 'not-an-id') },
  ])('retains every unrelated transport or provenance failure: %j', (changed) => {
    expect(isExpectedApiReadNavigationAbort({ ...run, ...changed }, API)).toBe(false)
  })
})
