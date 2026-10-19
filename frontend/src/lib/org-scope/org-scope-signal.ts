/**
 * Abort boundary for requests made on behalf of the active organization
 * (plan 13.10/13.11). Every org-scoped request — `apiFetch`, uploads, binary
 * loads, SSE streams (POST and GET resume) and LangGraph protocol traffic — is
 * linked to the current generation's signal.
 *
 * An organization switch runs in two phases:
 * - `beginOrgSwitch()` aborts the current generation and closes the gate:
 *   until the switch finishes, every org-scoped request gets an
 *   already-aborted signal, so nothing issued during the switch window can
 *   land in either organization's state.
 * - `finishOrgSwitch()` opens a new generation.
 *
 * A caller that captured an `OrgScopeToken` (a long-lived transport, a
 * 401 → refresh → retry) can never send under a later generation:
 * `signalForOrgScope` returns an aborted signal once the token is stale.
 *
 * Auth and organization-navigation endpoints are not org-scoped and are never
 * linked (aborting them would surface as a spurious sign-out or a failed
 * switch).
 */

const ORG_INDEPENDENT_PATH_PREFIXES = ['/api/auth/', '/api/me/organizations', '/api/me/active-org']

export interface OrgScopeToken {
  readonly generation: number
}

let generation = 0
let controller = new AbortController()
let switching = false

export function isOrgIndependentRequestPath(path: string): boolean {
  const pathname = toPathname(path)
  return ORG_INDEPENDENT_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(prefix),
  )
}

export function currentOrgScope(): OrgScopeToken {
  return { generation }
}

export function isOrgScopeCurrent(token: OrgScopeToken): boolean {
  return !switching && token.generation === generation
}

/** Abort the current generation and gate new org-scoped requests. */
export function beginOrgSwitch(): void {
  switching = true
  controller.abort(orgChangedReason())
}

/** Open a new generation once the switched organization's UI mounts. */
export function finishOrgSwitch(): void {
  generation += 1
  controller = new AbortController()
  switching = false
}

/**
 * Signal for a request issued under `token`: the caller's signal combined with
 * the generation's signal, or an already-aborted signal when the token is
 * stale or a switch is in progress. Org-independent paths get the caller's
 * signal unchanged.
 */
export function signalForOrgScope(
  token: OrgScopeToken,
  path: string,
  signal?: AbortSignal | null,
): AbortSignal | undefined {
  if (isOrgIndependentRequestPath(path)) return signal ?? undefined
  if (!isOrgScopeCurrent(token)) return AbortSignal.abort(orgChangedReason())
  const orgSignal = controller.signal
  if (!signal) return orgSignal
  return anySignal([signal, orgSignal])
}

/** `signalForOrgScope` for the current generation. */
export function withOrgScopeSignal(
  path: string,
  signal?: AbortSignal | null,
): AbortSignal | undefined {
  return signalForOrgScope(currentOrgScope(), path, signal)
}

function orgChangedReason(): DOMException {
  return new DOMException('Active organization changed', 'AbortError')
}

/**
 * `AbortSignal.any` with a fallback for runtimes that lack it. The fallback
 * detaches its listeners from every input as soon as the combined signal
 * settles, so long-lived inputs (the generation signal) do not accumulate
 * listeners from aborted requests.
 */
export function anySignal(
  signals: readonly AbortSignal[],
  native: boolean = typeof AbortSignal.any === 'function',
): AbortSignal {
  if (native) return AbortSignal.any([...signals])
  const combined = new AbortController()
  const detachers: (() => void)[] = []
  const settle = (reason: unknown) => {
    for (const detach of detachers) detach()
    detachers.length = 0
    combined.abort(reason)
  }
  for (const signal of signals) {
    if (signal.aborted) {
      settle(signal.reason)
      break
    }
    const onAbort = () => settle(signal.reason)
    signal.addEventListener('abort', onAbort, { once: true })
    detachers.push(() => signal.removeEventListener('abort', onAbort))
  }
  return combined.signal
}

function toPathname(path: string): string {
  try {
    return new URL(path, 'http://org-scope.local').pathname
  } catch {
    return path
  }
}
