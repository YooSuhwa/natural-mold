import type { DirectoryQuery, TenantOrganizationPageQuery } from '@/lib/types/organization'

export const organizationQueryKeys = {
  mine: ['me', 'organizations'] as const,
  tenantAll: (tenantId: string | null | undefined) => ['tenant-organizations', tenantId] as const,
  tenantList: (tenantId: string | null | undefined, query: TenantOrganizationPageQuery = {}) =>
    ['tenant-organizations', tenantId, query.cursor ?? null, query.limit ?? null] as const,
  orgAdminRoot: ['org-admin'] as const,
  orgAll: (orgId: string | null | undefined) => ['org-admin', orgId] as const,
  org: (orgId: string | null | undefined) => ['org-admin', orgId, 'info'] as const,
  directory: (
    orgId: string | null | undefined,
    kind: 'members' | 'groups' | 'roles' | 'capabilities',
    query: DirectoryQuery = {},
  ) => ['org-admin', orgId, kind, query.cursor ?? null, query.limit ?? null] as const,
  groupMembers: (orgId: string | null | undefined, groupId: string | null | undefined) =>
    ['org-admin', orgId, 'group-members', groupId] as const,
  principalGrants: (
    orgId: string | null | undefined,
    kind: 'roles' | 'capabilities',
    principalType: string,
    principalId: string,
  ) => ['org-admin', orgId, 'principal-grants', kind, principalType, principalId] as const,
  tenantOverview: (tenantId: string | null | undefined) =>
    ['tenant-organizations', tenantId, 'overview'] as const,
  principals: (orgId: string | null | undefined, q: string, types: string) =>
    ['org-admin', orgId, 'principals', q, types] as const,
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
  organizationQueryKeys.orgAdminRoot,
]

export function isOrgIndependentQueryKey(queryKey: readonly unknown[]): boolean {
  return ORG_INDEPENDENT_ROOTS.some((root) => root.every((part, index) => queryKey[index] === part))
}
