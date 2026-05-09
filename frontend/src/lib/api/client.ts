import { clearCsrfToken, getCsrfToken, setCsrfToken } from '@/lib/auth/csrf'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8001'

/** Endpoints that bypass automatic CSRF / 401-refresh handling. */
const AUTH_ENDPOINTS = [
  '/api/auth/login',
  '/api/auth/register',
  '/api/auth/refresh',
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

async function tryRefresh(): Promise<boolean> {
  if (refreshPromise) return refreshPromise
  refreshPromise = (async (): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!res.ok) return false
      const body = (await res.json().catch(() => null)) as RefreshBody | null
      if (body?.csrf_token) setCsrfToken(body.csrf_token)
      return true
    } catch {
      return false
    } finally {
      // Clear on next tick so concurrent awaits resolve against the same promise.
      setTimeout(() => {
        refreshPromise = null
      }, 0)
    }
  })()
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
function fireSessionExpired(): void {
  if (sessionExpiredFired) return
  sessionExpiredFired = true
  clearCsrfToken()
  try {
    sessionExpiredHandler?.()
  } finally {
    // Reset so a future successful login can fire again on next expiry.
    setTimeout(() => {
      sessionExpiredFired = false
    }, 1000)
  }
}

// ------------------------------ core fetch ---------------------------------

function isAuthEndpoint(path: string): boolean {
  return AUTH_ENDPOINTS.some((p) => path === p || path.startsWith(`${p}?`))
}

function buildHeaders(method: string, path: string, init?: HeadersInit): Headers {
  const headers = new Headers(init)
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')

  const upper = method.toUpperCase()
  if (MUTATION_METHODS.has(upper) && !isAuthEndpoint(path)) {
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

  // 401 → single refresh attempt + retry, except on the auth endpoints themselves.
  if (response.status === 401 && !isAuthEndpoint(path)) {
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

export { API_BASE, ApiError }
