/**
 * Session-expired latch. Holds two pieces of process-local state:
 *
 *  1. ``sessionExpiredHandler`` — the UI callback (toast + redirect)
 *     registered by ``QueryProvider``.
 *  2. ``fired`` — a one-shot flag so dozens of concurrent ``useQuery``
 *     401s only trigger one toast/redirect. Cleared on a successful
 *     login (``useLogin``/``useRegister`` call ``resetSessionExpiredFlag``).
 *
 * Lives in its own module so the auth path is testable without spinning
 * up the entire HTTP client + its CSRF/fetch state.
 */

import { clearCsrfToken } from './csrf'

type SessionExpiredHandler = () => void

export interface AuthGate {
  setHandler(handler: SessionExpiredHandler | null): void
  fire(): void
  reset(): void
}

/** Build an isolated gate. Production uses the module-level singleton
 *  ``authGate`` below; tests build their own to avoid cross-test bleed. */
export function createAuthGate(): AuthGate {
  let handler: SessionExpiredHandler | null = null
  let fired = false

  return {
    setHandler(next) {
      handler = next
    },
    fire() {
      if (fired) return
      fired = true
      clearCsrfToken()
      handler?.()
      // Hold the gate open until ``reset`` is called by a successful
      // login — otherwise concurrent useQuery 401s fire the toast/redirect
      // dozens of times.
    },
    reset() {
      fired = false
    },
  }
}

export const authGate = createAuthGate()
