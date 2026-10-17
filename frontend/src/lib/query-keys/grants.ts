import type { ShareResourceType } from '@/lib/types/grant'

/** Plan 13.1: `['grants', type, id]`; dropped on an organization switch (not retained). */
export const grantQueryKeys = {
  all: ['grants'] as const,
  resource: (type: ShareResourceType | 'marketplace_item', id: string) =>
    ['grants', type, id] as const,
  access: (type: ShareResourceType, id: string) => ['grants', type, id, 'access'] as const,
  /** H-05 per-row `my_permissions` of a page (`ids` = sorted, comma-joined). */
  permissions: (type: string, ids: string) => ['grants', type, 'permissions', ids] as const,
}
