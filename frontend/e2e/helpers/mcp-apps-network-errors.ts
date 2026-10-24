import { isExpectedNextRscPrefetchAbort } from './network-failure-diagnostic'

export const KNOWN_SHIM_BEACON_URL =
  'https://static.cloudflareinsights.com/beacon.min.js/v31edd6df95cf4e85bb4c19e7a9bdbcba1788362987495'

// Cloudflare rotates the beacon's version segment, so match its shape, not one hash.
const SHIM_BEACON_PATH = /^\/beacon\.min\.js\/v[0-9a-f]+$/

function isShimBeaconUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return (
      parsed.origin === 'https://static.cloudflareinsights.com' &&
      SHIM_BEACON_PATH.test(parsed.pathname) &&
      parsed.search === ''
    )
  } catch {
    return false
  }
}

export interface ConsoleErrorObservation {
  readonly text: string
  readonly locationUrl: string
}

export interface RequestFailureObservation {
  readonly url: string
  readonly errorText: string | null
  readonly method: string
  readonly resourceType: string
  readonly frameUrl: string | null
}

function isOfficialShimFrame(frameUrl: string | null, appOrigin: string): boolean {
  if (frameUrl === null) return false
  try {
    const frame = new URL(frameUrl)
    return (
      frame.protocol === 'https:' &&
      frame.hostname.endsWith('.scf.auiusercontent.com') &&
      frame.pathname.endsWith('/shim.html') &&
      frame.searchParams.get('origin') === appOrigin
    )
  } catch {
    return false
  }
}

export function isKnownShimConsoleError(observation: ConsoleErrorObservation): boolean {
  return (
    isShimBeaconUrl(observation.locationUrl) &&
    observation.text === 'Failed to load resource: net::ERR_NAME_NOT_RESOLVED'
  )
}

export function isKnownShimRequestFailure(
  observation: RequestFailureObservation,
  appOrigin: string,
): boolean {
  if (observation.frameUrl === null || !isOfficialShimFrame(observation.frameUrl, appOrigin)) {
    return false
  }
  if (
    isShimBeaconUrl(observation.url) &&
    observation.errorText === 'net::ERR_NAME_NOT_RESOLVED' &&
    observation.resourceType === 'script'
  ) {
    return true
  }
  if (
    observation.errorText !== 'net::ERR_ABORTED' ||
    observation.method !== 'POST' ||
    !['ping', 'xhr'].includes(observation.resourceType)
  ) {
    return false
  }
  try {
    const request = new URL(observation.url)
    const frame = new URL(observation.frameUrl)
    return request.origin === frame.origin && request.pathname === '/cdn-cgi/rum'
  } catch {
    return false
  }
}

function isExpectedMcpAppsRscPrefetchAbort(
  observation: RequestFailureObservation,
  appOrigin: string,
): boolean {
  return isExpectedNextRscPrefetchAbort({
    currentPageUrl: observation.frameUrl ?? appOrigin,
    errorText: observation.errorText ?? '',
    method: observation.method,
    requestUrl: observation.url,
    resourceType: observation.resourceType,
  })
}

export function unexpectedMcpAppsBrowserErrors(
  consoleErrors: readonly ConsoleErrorObservation[],
  requestFailures: readonly RequestFailureObservation[],
  appOrigin: string,
): {
  readonly consoleErrors: readonly ConsoleErrorObservation[]
  readonly requestFailures: readonly RequestFailureObservation[]
} {
  return {
    consoleErrors: consoleErrors.filter((error) => !isKnownShimConsoleError(error)),
    requestFailures: requestFailures.filter(
      (failure) =>
        !isKnownShimRequestFailure(failure, appOrigin) &&
        !isExpectedMcpAppsRscPrefetchAbort(failure, appOrigin),
    ),
  }
}
