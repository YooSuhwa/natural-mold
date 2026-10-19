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
 * Query roots kept across an organization switch: the session and the user's
 * own organization list (the switcher needs it while the new org loads).
 * Everything else — resource lists, org administration (keyed by the previous
 * org id) and the company directory (the target org may be in another
 * company) — is cancelled and removed (plan 13.11).
 */
const RETAINED_ACROSS_ORG_SWITCH: readonly (readonly string[])[] = [
  ['auth'],
  organizationQueryKeys.mine,
]

export function isRetainedAcrossOrgSwitch(queryKey: readonly unknown[]): boolean {
  return RETAINED_ACROSS_ORG_SWITCH.some((root) =>
    root.every((part, index) => queryKey[index] === part),
  )
}
