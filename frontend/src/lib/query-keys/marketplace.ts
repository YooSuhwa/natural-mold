import type { MarketplaceListFilters } from '@/lib/types/marketplace'

export const marketplaceQueryKeys = {
  items: ['marketplace', 'items'] as const,
  itemList: (filters?: MarketplaceListFilters) => ['marketplace', 'items', filters ?? {}] as const,
  itemPage: (filters?: MarketplaceListFilters) =>
    ['marketplace', 'items', 'page', filters ?? {}] as const,
  item: (itemId: string | null | undefined) => ['marketplace', 'items', itemId] as const,
  itemVersions: (itemId: string | null | undefined) =>
    ['marketplace', 'items', itemId, 'versions'] as const,
  version: (versionId: string | null | undefined) =>
    ['marketplace', 'versions', versionId] as const,
  kSkillAdmin: ['marketplace', 'admin', 'k-skill'] as const,
  moderation: ['marketplace', 'admin', 'moderation'] as const,
}
