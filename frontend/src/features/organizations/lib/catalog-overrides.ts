import type { CatalogItemView, CatalogOverride } from '@/lib/types/org-governance'

/**
 * The full catalog override list after setting one item (`PUT` is a full replacement).
 *
 * The view lists the whole company inventory and each item's stored organization
 * value, and the backend rejects overrides for ids outside that inventory (404), so
 * every storable override is visible here and none is dropped: an item off for the
 * organization stays an explicit `false` (also when the company has turned it off,
 * so it stays off if the company turns it back on). An item on for the organization
 * is the default — a stored explicit `true` is equivalent and is not resent.
 */
export function nextOverrides(
  items: readonly CatalogItemView[],
  target: CatalogItemView,
  enabled: boolean,
): CatalogOverride[] {
  return items.flatMap((item) => {
    const isTarget = item.type === target.type && item.id === target.id
    const on = isTarget ? enabled : item.organization_enabled
    return on ? [] : [{ type: item.type, id: item.id, enabled: false }]
  })
}
