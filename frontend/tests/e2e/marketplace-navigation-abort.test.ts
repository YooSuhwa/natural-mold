import { describe, expect, it } from 'vitest'
import {
  isExpectedMarketplaceVersionNavigationAbort,
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
    expect(isExpectedMarketplaceVersionNavigationAbort(input, API)).toBe(true)
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
    expect(isExpectedMarketplaceVersionNavigationAbort({ ...input, ...changed }, API)).toBe(false)
  })
})
