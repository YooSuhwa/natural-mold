'use client'

import { useCapability } from '@/lib/hooks/use-resource-permissions'

/** Publishing to the marketplace needs `cap_publish_marketplace` (plan 13.9). */
export function useCanPublishMarketplace(): boolean {
  return useCapability('cap_publish_marketplace')
}
