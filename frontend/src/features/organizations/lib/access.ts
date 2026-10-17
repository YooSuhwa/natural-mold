import type {
  OrgCapability,
  OrgControlPermission,
  OrganizationSummary,
} from '@/lib/types/organization'

export interface OrgAccess {
  /** Org admin: may manage members, groups, roles and org settings. */
  canManageMembers: boolean
  canManagePolicy: boolean
  /** Role/capability grant lists are served only to member managers (`org_members_context`). */
  canReadGrants: boolean
  /**
   * Org usage/limits read (`can_view_usage`, plan 9.6): org admin or auditor roles. Company
   * admins/auditors also pass on the server, but no client signal says so — fail closed.
   */
  canViewUsage: boolean
  hasCapability: (capability: OrgCapability) => boolean
}

/**
 * Gate UI actions by the caller's org roles/capabilities from
 * `GET /api/me/organizations`. When the org view has loaded, its server-computed
 * `permissions` (which also covers company administrators) wins.
 * The server still enforces everything; this only hides actions that would 403.
 */
export function orgAccess(
  summary: OrganizationSummary | null | undefined,
  permissions?: readonly OrgControlPermission[] | null,
): OrgAccess {
  const roles = summary?.roles ?? []
  const isAdmin = roles.includes('admin')
  const canManageMembers = permissions ? permissions.includes('can_manage_members') : isAdmin
  const canManagePolicy = permissions ? permissions.includes('can_manage_policy') : isAdmin
  const capabilities = summary?.capabilities ?? []
  return {
    canManageMembers,
    canManagePolicy,
    canReadGrants: canManageMembers,
    canViewUsage: isAdmin || roles.includes('auditor'),
    hasCapability: (capability) => capabilities.includes(capability),
  }
}
