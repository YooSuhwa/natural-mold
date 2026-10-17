import { ApiError } from './errors'

/**
 * One place that turns an API failure into what a screen shows (plan 13.1 "오류 처리", 8.4).
 *
 * Decisions follow `ApiError.code`, not the bare status: several routes answer 404 with
 * their own `<resource>_not_found` code and 403 means three different things.
 */
export type ApiErrorUiKind =
  | 'unauthenticated'
  | 'permission_required'
  | 'capability_required'
  | 'org_access_denied'
  | 'tenant_suspended'
  | 'not_found'
  | 'agent_not_ready'
  | 'quota_exceeded'
  | 'authz_unavailable'
  | 'unknown'

export interface ApiErrorUiState {
  kind: ApiErrorUiKind
  error: ApiError | null
  /** 403 `permission_required`: whether the server offers an access request (S18). */
  requestable: boolean
  /** 403 `capability_required`: the missing `cap_*`. */
  capability: string | null
  /** 409 `agent_not_ready`: the agent named by the body (config viewers only). */
  agentId: string | null
  /** Transient failures worth a retry button. */
  retryable: boolean
}

const RETRYABLE: ReadonlySet<ApiErrorUiKind> = new Set([
  'authz_unavailable',
  'quota_exceeded',
  'unknown',
])

function detail(error: ApiError, key: string): string | null {
  const value = error.details?.[key]
  return typeof value === 'string' ? value : null
}

/** 404 with a not-found code (`not_found`, `AGENT_NOT_FOUND`, `marketplace_item_not_found` …). */
export function isNotFoundError(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 404 && /not_found$/i.test(error.code)
}

const CODE_KINDS: ReadonlySet<string> = new Set<ApiErrorUiKind>([
  'permission_required',
  'capability_required',
  'org_access_denied',
  'tenant_suspended',
  'agent_not_ready',
  'quota_exceeded',
  'authz_unavailable',
])

function kindOf(error: ApiError): ApiErrorUiKind {
  if (CODE_KINDS.has(error.code)) return error.code as ApiErrorUiKind
  if (error.status === 401) return 'unauthenticated'
  if (error.status === 429) return 'quota_exceeded'
  if (isNotFoundError(error)) return 'not_found'
  return 'unknown'
}

export function mapApiErrorToUiState(error: unknown): ApiErrorUiState {
  const apiError = error instanceof ApiError ? error : null
  const kind = apiError ? kindOf(apiError) : 'unknown'
  return {
    kind,
    error: apiError,
    requestable: kind === 'permission_required' && apiError?.details?.request_access !== false,
    capability: apiError && kind === 'capability_required' ? detail(apiError, 'capability') : null,
    agentId: apiError && kind === 'agent_not_ready' ? detail(apiError, 'agent_id') : null,
    retryable: RETRYABLE.has(kind),
  }
}
