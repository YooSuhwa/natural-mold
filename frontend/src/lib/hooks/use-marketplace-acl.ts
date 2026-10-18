'use client'

import { useQuery } from '@tanstack/react-query'

import { marketplaceApi } from '@/lib/api/marketplace'
import { marketplaceQueryKeys } from '@/lib/query-keys/marketplace'

/** Named ACL recipients of an item the caller manages (S16 marketplace share dialog). */
export function useMarketplaceItemAcl(itemId: string, enabled: boolean) {
  return useQuery({
    queryKey: marketplaceQueryKeys.itemAcl(itemId),
    queryFn: () => marketplaceApi.listACL(itemId),
    enabled,
  })
}
