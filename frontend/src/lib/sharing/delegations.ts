import { ApiError } from '@/lib/api/errors'
import type { DelegatedAccount } from '@/lib/types/grant'

/** `confirm_required` → the delegated account resources to acknowledge (D17). */
export function delegationsToConfirm(error: unknown): DelegatedAccount[] | null {
  if (!(error instanceof ApiError) || error.code !== 'confirm_required') return null
  const raw = error.details?.delegations
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item: unknown) => {
    if (!item || typeof item !== 'object') return []
    const record = item as Record<string, unknown>
    const { resource_type, resource_id, name } = record
    if (typeof resource_type !== 'string' || typeof resource_id !== 'string') return []
    const account: DelegatedAccount = {
      resource_type,
      resource_id,
      name: typeof name === 'string' ? name : resource_id,
    }
    // E-06: which credential and whose account, when the backend says so.
    for (const key of ['credential_id', 'credential_name', 'owner_name'] as const) {
      const value = record[key]
      if (typeof value === 'string') account[key] = value
    }
    return [account]
  })
}
