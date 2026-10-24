import type { Page } from '@playwright/test'

/** Every per-resource permission an owner holds (H-05 `my_permissions`). */
export const OWNER_PERMISSIONS = [
  'can_use',
  'can_view',
  'can_view_config',
  'can_edit',
  'can_share',
  'can_revoke',
  'can_delete',
] as const

/**
 * Specs that mock resource APIs with fixture ids must also answer the H-05 batch lookup
 * (`GET /api/resources/{type}/permissions?ids=…`): the real backend rejects non-UUID ids
 * with 422 and the screens then fail closed (disabled or hidden owner actions).
 */
export async function mockOwnerResourcePermissions(page: Page): Promise<void> {
  await page.route('**/api/resources/*/permissions?*', (route) => {
    const ids = new URL(route.request().url()).searchParams.getAll('ids')
    return route.fulfill({
      json: { items: ids.map((id) => ({ id, my_permissions: [...OWNER_PERMISSIONS] })) },
    })
  })
}
