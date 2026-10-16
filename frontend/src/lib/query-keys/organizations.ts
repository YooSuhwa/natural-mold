import type { TenantOrganizationPageQuery } from '@/lib/types/organization'

export const organizationQueryKeys = {
  mine: ['me', 'organizations'] as const,
  tenantAll: (tenantId: string | null | undefined) => ['tenant-organizations', tenantId] as const,
  tenantList: (tenantId: string | null | undefined, query: TenantOrganizationPageQuery = {}) =>
    ['tenant-organizations', tenantId, query.cursor ?? null, query.limit ?? null] as const,
}

/**
 * Query roots that do not depend on the active organization. Everything else
 * (agents, conversations, credentials, ...) is org-scoped on the backend and
 * must be reset when the active organization changes.
 */
const ORG_INDEPENDENT_ROOTS: readonly (readonly string[])[] = [
  ['auth'],
  organizationQueryKeys.mine,
  ['tenant-organizations'],
]

export function isOrgIndependentQueryKey(queryKey: readonly unknown[]): boolean {
  return ORG_INDEPENDENT_ROOTS.some((root) => root.every((part, index) => queryKey[index] === part))
}
