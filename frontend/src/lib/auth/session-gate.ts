/**
 * Session-expired latch. Two pieces of process-local state:
 *
 *  1. ``handler`` — the UI callback (toast + redirect) registered by
 *     ``QueryProvider``.
 *  2. ``fired`` — a one-shot flag so dozens of concurrent ``useQuery``
 *     401s only trigger one toast/redirect. Cleared on a successful
 *     login (``useLogin``/``useRegister`` call ``authGate.reset()``).
 *
 * Lives in its own module so the auth path is testable without spinning
 * up the entire HTTP client + its CSRF/fetch state.
 */

import { csrfStore } from './csrf'

type SessionExpiredHandler = () => void

let handler: SessionExpiredHandler | null = null
let fired = false

export const authGate = {
  setHandler(next: SessionExpiredHandler | null) {
    handler = next
  },
  fire() {
    if (fired) return
    fired = true
    csrfStore.clear()
    handler?.()
    // Hold the gate open until ``reset`` is called by a successful
    // login — otherwise concurrent useQuery 401s fire the toast/redirect
    // dozens of times.
  },
  reset() {
    fired = false
  },
}
