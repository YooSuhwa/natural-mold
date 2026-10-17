/**
 * `['notifications', orgId, ...]` (plan 13.8). Not retained across an organization
 * switch: the 13.11 purge removes every root outside `isRetainedAcrossOrgSwitch`.
 */
export const notificationQueryKeys = {
  all: ['notifications'] as const,
  org: (orgId: string | null | undefined) => ['notifications', orgId ?? null] as const,
  list: (orgId: string | null | undefined) => ['notifications', orgId ?? null, 'list'] as const,
  unread: (orgId: string | null | undefined) =>
    ['notifications', orgId ?? null, 'unread-count'] as const,
}
