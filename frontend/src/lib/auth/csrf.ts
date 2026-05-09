/**
 * CSRF token storage — in-memory primary, cookie (`moldy_csrf`) fallback.
 *
 * Backend uses double-submit pattern: the same value is set as a non-HttpOnly
 * cookie *and* returned in the response body. Either source works; we prefer
 * the in-memory value for predictability (avoids cookie-parsing edge cases),
 * and fall back to the cookie when the SPA boots without a fresh login.
 */

const COOKIE_NAME = 'moldy_csrf'

let inMemoryToken: string | null = null

export function setCsrfToken(token: string | null): void {
  inMemoryToken = token
}

export function clearCsrfToken(): void {
  inMemoryToken = null
}

export function getCsrfToken(): string | null {
  if (inMemoryToken) return inMemoryToken
  if (typeof document === 'undefined') return null
  const cookie = readCookie(COOKIE_NAME)
  if (cookie) inMemoryToken = cookie
  return cookie
}

function readCookie(name: string): string | null {
  // Use a single split + find — small cookie strings make this O(n) anyway.
  const cookies = document.cookie ? document.cookie.split('; ') : []
  for (const part of cookies) {
    const eq = part.indexOf('=')
    if (eq === -1) continue
    if (part.slice(0, eq) === name) {
      return decodeURIComponent(part.slice(eq + 1))
    }
  }
  return null
}
