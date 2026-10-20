import type { OrganizationSummary, TenantOverview } from '@/lib/types/organization'

/**
 * A personal company whose owner is still its only active member. Screens collapse the
 * company/organization administration for it; the routes and permissions stay as they are.
 */
export function isSoloWorkspace(
  organization: OrganizationSummary | null | undefined,
  overview: Pick<TenantOverview, 'active_member_count'> | undefined,
): boolean {
  return organization?.tenant_kind === 'personal' && overview?.active_member_count === 1
}

/** Company members screen with the invitation dialog open. */
export const MAKE_TEAM_HREF = '/settings/company/members?invite=1'
