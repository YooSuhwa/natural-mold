'use client'

import { useCallback, useMemo } from 'react'

import { useResourcePermissions } from '@/lib/hooks/use-resource-permissions'

export interface MarketplaceItemAccess {
  /** Owner or manager grant: visibility, unpublish, ACL (plan 13.9 `can_manage`). */
  canManage: boolean
  /** `can_install` (plan 13.9). */
  canInstall: boolean
}

/**
 * Item actions from the server's `my_permissions` (`GET /api/resources/marketplace_item/
 * permissions`: `can_view` / `can_install` / `can_manage`). Fails closed: until the answer
 * arrives and when it failed (503), management stays hidden; install stays offered — it
 * is the listed item's use, and the install route decides. An item the server left out
 * of a successful answer gets neither.
 */
export function marketplaceItemAccess(
  permissions: readonly string[] | undefined,
  answered: boolean,
): MarketplaceItemAccess {
  if (permissions) {
    return {
      canManage: permissions.includes('can_manage'),
      canInstall: permissions.includes('can_install'),
    }
  }
  return { canManage: false, canInstall: !answered }
}

/** `marketplaceItemAccess` for every listed item, one permissions request per page. */
export function useMarketplaceItemAccess(ids: readonly (string | null | undefined)[]) {
  const { permissionsOf, answered, error, refetch } = useResourcePermissions(
    'marketplace_item',
    ids,
  )
  const accessOf = useCallback(
    (id: string | null | undefined) => marketplaceItemAccess(permissionsOf(id), answered),
    [answered, permissionsOf],
  )
  return useMemo(() => ({ accessOf, error, refetch }), [accessOf, error, refetch])
}
