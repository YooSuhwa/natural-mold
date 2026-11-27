import { currentOrgScope, isOrgScopeCurrent } from '@/lib/org-scope/org-scope-signal'

// Store only a fingerprint and request ID so a lost response can be retried after reload.
export async function pendingJobRequest(
  runId: string,
  targetId: string,
  instruction: string,
  pending: Map<string, string>,
  childTaskId?: string,
) {
  const scope = currentOrgScope()
  const payload = JSON.stringify([runId, targetId, childTaskId ?? null, instruction])
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(payload))
  if (!isOrgScopeCurrent(scope)) throw new DOMException('Organization changed', 'AbortError')
  const digest = Array.from(new Uint8Array(hash), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('')
  const key = `moldy.independent-job:${digest}`
  let id = pending.get(key) ?? crypto.randomUUID()
  try {
    id = sessionStorage.getItem(key) ?? id
    sessionStorage.setItem(key, id)
  } catch {
    // Browsers with blocked site data still deduplicate retries in the open form.
  }
  pending.set(key, id)
  return {
    id,
    confirm() {
      pending.delete(key)
      try {
        sessionStorage.removeItem(key)
      } catch {
        // The in-memory receipt has already been cleared.
      }
    },
  }
}
