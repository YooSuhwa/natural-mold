import { isExpectedNextRscPrefetchAbort } from './network-failure-diagnostic'

export const KNOWN_SHIM_BEACON_URL =
  'https://static.cloudflareinsights.com/beacon.min.js/v31edd6df95cf4e85bb4c19e7a9bdbcba1788362987495'

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
    observation.locationUrl === KNOWN_SHIM_BEACON_URL &&
    observation.text === 'Failed to load resource: net::ERR_NAME_NOT_RESOLVED'
  )
}

export function isKnownShimRequestFailure(
  observation: RequestFailureObservation,
  appOrigin: string,
): boolean {
  return (
    observation.url === KNOWN_SHIM_BEACON_URL &&
    observation.errorText === 'net::ERR_NAME_NOT_RESOLVED' &&
    observation.resourceType === 'script' &&
    isOfficialShimFrame(observation.frameUrl, appOrigin)
  )
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
