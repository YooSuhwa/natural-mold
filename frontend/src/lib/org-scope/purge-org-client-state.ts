import type { QueryClient } from '@tanstack/react-query'

import { isRetainedAcrossOrgSwitch } from '@/lib/query-keys/organizations'
import { resetOrgScopedModuleState } from './org-scope-reset'
import { clearOrgScopedStorage } from './org-scoped-storage'

/**
 * Drop every piece of client state that can carry the previous organization's
 * ids (plan 13.11): cancel and remove org-scoped queries, clear org-scoped web
 * storage and module-level caches. Run after the org subtree has unmounted so
 * no observer re-creates a query from the previous organization.
 */
export async function purgeOrgClientState(queryClient: QueryClient): Promise<void> {
  const isOrgScoped = (query: { queryKey: readonly unknown[] }) =>
    !isRetainedAcrossOrgSwitch(query.queryKey)
  await queryClient.cancelQueries({ predicate: isOrgScoped })
  queryClient.removeQueries({ predicate: isOrgScoped })
  clearOrgScopedStorage()
  resetOrgScopedModuleState()
}
