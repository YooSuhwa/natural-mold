/**
 * Permission gate for screen actions (plan 13.1 "권한 게이트", 13.9).
 *
 * Shadow/enforce responses carry `my_permissions` (agents in their bodies; tools, MCP
 * servers, credentials and skills through `GET /api/resources/{type}/permissions`).
 * Legacy bodies carry none and legacy lists only hold the caller's own rows plus
 * platform rows, so the owner decides: the owner holds everything, a platform row may
 * be used and viewed, and a row with an unknown owner counts as the caller's own.
 * The server still enforces every action; this only hides what would 403.
 */
export interface PermissionSource {
  myPermissions?: readonly string[] | null
  /** `undefined` = the response has no owner field (own rows only in legacy). */
  ownerId?: string | null
  isSystem?: boolean
}

const PLATFORM_PERMISSIONS: ReadonlySet<string> = new Set(['can_use', 'can_view', 'can_run'])

export function hasPermission(
  source: PermissionSource,
  permission: string,
  currentUserId: string | null | undefined,
): boolean {
  if (source.myPermissions) return source.myPermissions.includes(permission)
  if (source.isSystem) return PLATFORM_PERMISSIONS.has(permission)
  if (source.ownerId === undefined) return true
  return !!source.ownerId && source.ownerId === currentUserId
}

/** Agent bodies: `my_permissions` outside legacy; a runner view never has the config. */
export function agentPermissions(
  agent: { my_permissions?: readonly string[] | null; view?: string } | null | undefined,
): readonly string[] | null {
  if (!agent) return null
  if (agent.my_permissions) return agent.my_permissions
  return agent.view === 'runner' ? ['can_run'] : null
}

/** Shorthand for agents: `null` permissions (legacy body) allow everything. */
export function agentCan(
  agent: { my_permissions?: readonly string[] | null; view?: string } | null | undefined,
  permission: string,
): boolean {
  const permissions = agentPermissions(agent)
  return permissions ? permissions.includes(permission) : true
}
