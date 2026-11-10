import type { RegistrationState } from '@/lib/types/approval'

/**
 * Rows an agent may attach: a tool waiting for (or refused) the administrators' approval
 * stays out of pickers — the server refuses the attach with 409 anyway. Rows in `keep`
 * (already attached) stay listed so they can still be removed.
 */
export function attachable<T extends { id: string; registration_state?: RegistrationState }>(
  rows: readonly T[],
  keep?: ReadonlySet<string>,
): T[] {
  return rows.filter(
    (row) => (row.registration_state ?? 'approved') === 'approved' || !!keep?.has(row.id),
  )
}
