/**
 * CSRF token store — in-memory primary, cookie (`moldy_csrf`) fallback.
 *
 * Backend uses double-submit pattern: the same value is set as a non-HttpOnly
 * cookie *and* returned in the response body. Either source works; we prefer
 * the in-memory value for predictability (avoids cookie-parsing edge cases)
 * and fall back to the cookie when the SPA boots without a fresh login.
 *
 * Shape mirrors ``authGate`` in ``./session-gate.ts`` — module-level let
 * state grouped into a single object literal — so both auth utilities
 * have the same import/use ergonomics.
 */

const COOKIE_NAME = 'moldy_csrf'

let inMemoryToken: string | null = null

function readCookie(name: string): string | null {
  // Single split + find — cookie strings are short enough that O(n) is fine.
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

export const csrfStore = {
  set(token: string) {
    inMemoryToken = token
  },
  clear() {
    inMemoryToken = null
  },
  get(): string | null {
    if (inMemoryToken) return inMemoryToken
    if (typeof document === 'undefined') return null
    // First read after page load — warm the in-memory cache from the
    // double-submit cookie so subsequent reads skip the parse.
    const cookie = readCookie(COOKIE_NAME)
    if (cookie) inMemoryToken = cookie
    return cookie
  },
}
