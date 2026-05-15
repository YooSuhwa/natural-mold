import { clearCsrfToken, getCsrfToken, setCsrfToken } from '@/lib/auth/csrf'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8001'

// Endpoints that own the auth lifecycle themselves. Logout is the only
// asymmetry: it still needs CSRF (cross-origin POST shouldn't force-logout)
// but must skip the 401 → refresh chain (refresh attempt with an already-
// expired token would just fire the session-expired toast mid-logout).
// Add new auth endpoints here — both arrays derive from this base.
const AUTH_PREAUTH_ENDPOINTS = [
  '/api/auth/login',
  '/api/auth/register',
  '/api/auth/refresh',
] as const
const CSRF_SKIP_ENDPOINTS = AUTH_PREAUTH_ENDPOINTS
const REFRESH_SKIP_ENDPOINTS = [
  ...AUTH_PREAUTH_ENDPOINTS,
  '/api/auth/logout',
] as const

/** HTTP methods considered state-changing for CSRF purposes. */
const MUTATION_METHODS = new Set(['POST', 'PATCH', 'PUT', 'DELETE'])

class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

interface RefreshBody {
  csrf_token?: string
}

// ------------------------------ refresh dedup ------------------------------

let refreshPromise: Promise<boolean> | null = null
// After a refresh failure (401/429/network), refuse new attempts for this
// window so dozens of concurrent useQuery hooks don't pound /refresh and
// trip the rate limiter — each fresh attempt would just keep returning 401
// since the refresh cookie itself is stale. Cleared on a successful login.
const REFRESH_FAIL_BACKOFF_MS = 5_000
let lastRefreshFailureAt = 0

export function resetRefreshBackoff(): void {
  lastRefreshFailureAt = 0
}

async function tryRefresh(): Promise<boolean> {
  if (refreshPromise) return refreshPromise
  if (Date.now() - lastRefreshFailureAt < REFRESH_FAIL_BACKOFF_MS) {
    return false
  }
  // ``finally`` runs synchronously when the inner promise settles, so
  // every concurrent awaiter (queued in the same microtask) sees the
  // shared ``refreshPromise`` before it's cleared.
  refreshPromise = (async (): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!res.ok) {
        lastRefreshFailureAt = Date.now()
        return false
      }
      const body = (await res.json().catch(() => null)) as RefreshBody | null
      if (body?.csrf_token) setCsrfToken(body.csrf_token)
      return true
    } catch {
      lastRefreshFailureAt = Date.now()
      return false
    }
  })().finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

// ------------------------------ session-expired hook ----------------------

type SessionExpiredHandler = () => void
let sessionExpiredHandler: SessionExpiredHandler | null = null

/**
 * Registered by the QueryProvider on the client. Invoked exactly once per
 * unrecoverable 401 chain (refresh failed) so the UI can clear cache + toast +
 * redirect to /login.
 */
export function setSessionExpiredHandler(handler: SessionExpiredHandler | null): void {
  sessionExpiredHandler = handler
}

let sessionExpiredFired = false
export function fireSessionExpired(): void {
  if (sessionExpiredFired) return
  sessionExpiredFired = true
  clearCsrfToken()
  try {
    sessionExpiredHandler?.()
  } finally {
    // Hold the gate open until a successful login (``resetRefreshBackoff``)
    // clears it — otherwise concurrent useQuery 401s fire the toast/redirect
    // dozens of times. ``useLogin``/``useRegister`` clear this state.
  }
}

export function resetSessionExpiredFlag(): void {
  sessionExpiredFired = false
}

// ------------------------------ core fetch ---------------------------------

function matchesEndpoint(path: string, list: readonly string[]): boolean {
  return list.some((p) => path === p || path.startsWith(`${p}?`))
}

function buildHeaders(method: string, path: string, init?: HeadersInit): Headers {
  const headers = new Headers(init)
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')

  const upper = method.toUpperCase()
  if (MUTATION_METHODS.has(upper) && !matchesEndpoint(path, CSRF_SKIP_ENDPOINTS)) {
    const csrf = getCsrfToken()
    if (csrf && !headers.has('X-CSRF-Token')) {
      headers.set('X-CSRF-Token', csrf)
    }
  }
  return headers
}

async function rawFetch(path: string, options: RequestInit | undefined): Promise<Response> {
  const method = (options?.method ?? 'GET').toUpperCase()
  return fetch(`${API_BASE}${path}`, {
    ...options,
    method,
    credentials: 'include',
    headers: buildHeaders(method, path, options?.headers),
  })
}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  let response = await rawFetch(path, options)

  // 401 → single refresh attempt + retry, except on endpoints that own the
  // refresh lifecycle themselves (login/register/refresh) or that should be
  // allowed to complete locally even when expired (logout).
  if (response.status === 401 && !matchesEndpoint(path, REFRESH_SKIP_ENDPOINTS)) {
    const refreshed = await tryRefresh()
    if (refreshed) {
      response = await rawFetch(path, options)
    } else {
      fireSessionExpired()
    }
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = body?.detail ?? body?.error ?? {}
    const code: string =
      typeof detail === 'object' && detail
        ? (detail.code ?? 'UNKNOWN_ERROR')
        : (body.code ?? 'UNKNOWN_ERROR')
    const message: string =
      typeof detail === 'object' && detail
        ? (detail.message ?? body.message ?? response.statusText)
        : typeof detail === 'string'
          ? detail
          : (body.message ?? response.statusText)
    throw new ApiError(response.status, code, message)
  }

  if (response.status === 204) return undefined as T
  return response.json()
}

/**
 * Upload a ``FormData`` payload (multipart/form-data) with cookie auth,
 * CSRF header, and the same 401 → refresh → retry semantics as ``apiFetch``.
 *
 * ``apiFetch`` can't be reused here because it stamps
 * ``Content-Type: application/json`` which clobbers the multipart boundary
 * the browser auto-generates for ``FormData``.
 */
export async function apiUpload<T>(
  path: string,
  formData: FormData,
  signal?: AbortSignal,
): Promise<T> {
  const send = async (): Promise<Response> => {
    const headers: Record<string, string> = {}
    const csrf = getCsrfToken()
    if (csrf) headers['X-CSRF-Token'] = csrf
    return fetch(`${API_BASE}${path}`, {
      method: 'POST',
      credentials: 'include',
      headers,
      body: formData,
      signal,
    })
  }

  let response = await send()
  if (response.status === 401 && !matchesEndpoint(path, REFRESH_SKIP_ENDPOINTS)) {
    const refreshed = await tryRefresh()
    if (refreshed) {
      response = await send()
    } else {
      fireSessionExpired()
    }
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      error?: { code?: string; message?: string }
      detail?: { code?: string; message?: string } | string
      code?: string
      message?: string
    }
    const detail = body?.detail ?? body?.error ?? {}
    const code: string =
      typeof detail === 'object' && detail
        ? (detail.code ?? 'UPLOAD_ERROR')
        : (body.code ?? 'UPLOAD_ERROR')
    const message: string =
      typeof detail === 'object' && detail
        ? (detail.message ?? body.message ?? response.statusText)
        : typeof detail === 'string'
          ? detail
          : (body.message ?? response.statusText)
    throw new ApiError(response.status, code, message)
  }

  if (response.status === 204) return undefined as T
  return response.json()
}

export { API_BASE, ApiError }
