/**
 * Whether the share entry point is offered (plan 13.4: "공유는 can_share").
 *
 * Shadow/enforce responses carry `my_permissions`; legacy bodies do not, and in
 * legacy mode only the owner shares (E-01), so the owner id decides — or, for
 * lists that only ever return the caller's own rows (legacy agents), `true`.
 */
export function canShareResource({
  myPermissions,
  ownerId,
  currentUserId,
}: {
  myPermissions?: readonly string[] | null
  ownerId?: string | null
  currentUserId: string | null | undefined
}): boolean {
  if (myPermissions) return myPermissions.includes('can_share')
  if (ownerId !== undefined) return !!ownerId && ownerId === currentUserId
  return true
}

/** "공유받음": the row belongs to someone else. Unknown owners are never flagged. */
export function isSharedWithMe(
  ownerId: string | null | undefined,
  currentUserId: string | null | undefined,
): boolean {
  return !!ownerId && !!currentUserId && ownerId !== currentUserId
}
