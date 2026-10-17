import type { MarketplaceItem } from '@/lib/types/marketplace'

/** Shadow/enforce bodies may add `my_permissions` (`can_view`/`can_install`/`can_manage`). */
export type MarketplaceItemWithPermissions = MarketplaceItem & { my_permissions?: string[] }

export interface MarketplaceItemAccess {
  /** Owner or manager grant: visibility, unpublish, ACL (plan 13.9 `can_manage`). */
  canManage: boolean
  /** `can_install` (plan 13.9). */
  canInstall: boolean
}

/**
 * Item actions by permission. Shadow/enforce bodies may carry `my_permissions`
 * (`can_view` / `can_install` / `can_manage`); legacy bodies don't, so the legacy rule
 * applies: the owner manages (`publication_summary.item_id` is filled only for the
 * owner's own linked item) and anyone who sees an item may install it.
 */
export function marketplaceItemAccess(
  item: MarketplaceItemWithPermissions,
  userId: string | null | undefined,
): MarketplaceItemAccess {
  const permissions = item.my_permissions
  if (permissions) {
    return {
      canManage: permissions.includes('can_manage'),
      canInstall: permissions.includes('can_install'),
    }
  }
  return { canManage: !!userId && item.publication_summary.item_id === item.id, canInstall: true }
}
