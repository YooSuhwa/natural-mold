import type { Page } from '@playwright/test'

/**
 * U-01 platform operator screens need DEPLOYMENT_MODE=multi_tenant, which the scripted
 * lanes do not set. The screens are then played over `page.route` with the backend shapes
 * (`AdminTenantDetail`, `UsageLimitView`, installation capabilities).
 */

const now = Date.now()
const iso = (days: number) => new Date(now - days * 86_400_000).toISOString()

const TENANTS = [
  ['stub-tenant-1', '한빛물산', 'hanbit', 'active', 50, '500', 5, 12],
  ['stub-tenant-2', '세종에듀', 'sejong-edu', 'active', 200, '1500', 10, 87],
  ['stub-tenant-3', '바다물류', 'bada-logis', 'suspended', 30, null, 3, 9],
] as const

function tenant(row: (typeof TENANTS)[number], index: number) {
  const [id, name, slug, status, members, spend, runs, active] = row
  return {
    id,
    name,
    slug,
    status,
    created_at: iso(40 - index),
    updated_at: iso(1),
    limits: {
      member_limit: members,
      monthly_spend_limit: spend,
      concurrent_run_limit: runs,
      allow_system_models: true,
    },
    organization_count: index + 1,
    active_member_count: active,
  }
}

const ITEMS = TENANTS.map(tenant)

export async function installPlatformTenantStubs(page: Page): Promise<void> {
  await page.route('**/api/me/installation', (route) =>
    route.fulfill({
      json: {
        deployment_mode: 'multi_tenant',
        org_multi_enabled: true,
        platform_tenant_management_enabled: true,
      },
    }),
  )
  await page.route(
    (url) => url.pathname.startsWith('/api/admin/tenants'),
    (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      const parts = new URL(route.request().url()).pathname.split('/').slice(4)
      if (parts.length === 0) {
        return route.fulfill({ json: { items: ITEMS, next_cursor: null, has_more: false } })
      }
      const item = ITEMS.find((row) => row.id === parts[0]) ?? ITEMS[0]
      if (parts[1] === 'limits') {
        return route.fulfill({
          json: {
            used: '123.40',
            monthly_spend_limit: item.limits.monthly_spend_limit,
            resets_at: new Date(now + 12 * 86_400_000).toISOString(),
            member_limit: item.limits.member_limit,
            members: item.active_member_count,
            concurrent_run_limit: item.limits.concurrent_run_limit,
            active_runs: 1,
          },
        })
      }
      if (parts[1] === 'deletion') {
        return route.fulfill({ json: { tenant: item, deletion_due_at: null } })
      }
      if (parts[1] === 'exports') return route.fulfill({ json: { items: [] } })
      return route.fulfill({ json: item })
    },
  )
}
