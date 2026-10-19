'use client'

import { useMarketplaceItemAcl } from '@/lib/hooks/use-marketplace-acl'
import { useRemoveItemACLEntry } from '@/lib/hooks/use-marketplace'
import type { MarketplaceItem } from '@/lib/types/marketplace'

import type { ShareBackend, ShareEntry } from './share-backend'

/**
 * Marketplace items keep their ACL endpoints until I-04 (E-01 excludes them: the
 * next ACL sync would revoke a generic grant). Revoke-only: the ACL replace route
 * rewrites every row with one permission and the item does not expose per-row
 * permissions, so adding through it would silently change existing recipients.
 */

export function useMarketplaceShareBackend(item: MarketplaceItem): ShareBackend {
  const remove = useRemoveItemACLEntry(item.id)
  const recipients = item.acl_user_ids ?? []
  // The item carries ids only; names come from the manager-only ACL list.
  const acl = useMarketplaceItemAcl(item.id, recipients.length > 0)
  const names = new Map((acl.data?.items ?? []).map((row) => [row.user_id, row.display_name]))

  const entries: ShareEntry[] = recipients.map((userId) => ({
    key: `direct:user:${userId}`,
    revokeId: userId,
    principal: {
      type: 'user',
      id: userId,
      displayName: names.get(userId) ?? null,
      memberCount: null,
    },
    level: null,
    source: 'direct',
    revocable: true,
    expiresAt: null,
  }))

  return {
    surface: 'marketplace_item',
    snapshot: { entries, canShare: true, canRevoke: true, canGovern: false },
    isLoading: acl.isLoading,
    error: null,
    refetch: () => void acl.refetch(),
    allowsAdd: false,
    levels: [],
    principalTypes: ['user'],
    allowsExpiry: () => false,
    share: async () => {
      throw new Error('marketplace recipients are added at publish time until I-04')
    },
    revoke: async (entry) => {
      if (entry.revokeId) await remove.mutateAsync(entry.revokeId)
    },
    transfer: null,
    pending: remove.isPending,
  }
}
