import type { BrowserContext, Page, Route } from '@playwright/test'

import { test, expect } from './fixtures'

// Plan 16.5 E2E #7 (H-01): "접근 요청 → 관리자 승인 → 즉시 사용".
// A member who may only *use* a skill opens it (403 permission_required), asks for "편집",
// the skill's manager approves it in the S18 inbox, and the member's reload shows the skill.
//
// The org-authz role fixtures of 16.5 (admin/builder/member storageState on an enforce-mode
// stack with OpenFGA) do not exist yet, so the access API is played by an in-memory server
// over `page.route`. Opt in with E2E_ORG_AUTHZ=1; NOT run in the default lanes yet.

const SKILL_ID = '00000000-0000-4000-8000-0000000000a1'
const ORG = {
  id: '00000000-0000-4000-8000-0000000000b1',
  tenant_id: '00000000-0000-4000-8000-0000000000c1',
  name: 'Sales',
  slug: 'sales',
  is_active: true,
  is_default: true,
  status: 'active',
  roles: ['member'],
  capabilities: [],
}

interface AccessRequestRow {
  id: string
  resource_type: 'skill'
  resource_id: string
  resource_name: string
  level: string
  reason: string | null
  status: string
  requester: { id: string; display_name: string }
  decided_by: string | null
  decided_at: string | null
  decision_note: string | null
  granted_level: string | null
  created_at: string
  expires_at: string
}

/** The backend's side of the scenario: one skill, one request queue, one grant flag. */
function accessServer() {
  const state = { granted: false, requests: [] as AccessRequestRow[] }
  const skill = {
    id: SKILL_ID,
    name: '영업 리포트',
    slug: 'sales-report',
    description: 'Quarterly report',
    kind: 'text',
    version: null,
    storage_path: null,
    content_hash: 'h1',
    size_bytes: 10,
    used_by_count: 0,
    package_metadata: null,
    last_modified_at: '2026-10-01T00:00:00Z',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
  }

  async function install(target: Page | BrowserContext, as: 'member' | 'manager') {
    await target.route('**/api/me/organizations', (route) => route.fulfill({ json: [ORG] }))
    await target.route(`**/api/skills/${SKILL_ID}`, (route) =>
      as === 'manager' || state.granted
        ? route.fulfill({ json: skill })
        : route.fulfill({
            status: 403,
            json: {
              error: {
                code: 'permission_required',
                message: 'x',
                details: { required: 'can_view', request_access: true },
              },
            },
          }),
    )
    await target.route('**/api/access-requests**', (route) => handle(route, as))
  }

  async function handle(route: Route, as: 'member' | 'manager') {
    const request = route.request()
    const url = new URL(request.url())
    if (request.method() === 'POST' && url.pathname.endsWith('/approve')) {
      const row = state.requests.find((item) => url.pathname.includes(item.id))
      if (!row) return route.fulfill({ status: 404, json: { error: { code: 'not_found' } } })
      Object.assign(row, { status: 'approved', granted_level: row.level })
      state.granted = true
      return route.fulfill({ json: row })
    }
    if (request.method() === 'POST') {
      const body = request.postDataJSON() as { level: string; reason: string | null }
      const row: AccessRequestRow = {
        id: `req-${state.requests.length + 1}`,
        resource_type: 'skill',
        resource_id: SKILL_ID,
        resource_name: skill.name,
        level: body.level,
        reason: body.reason,
        status: 'pending',
        requester: { id: 'member-1', display_name: '정우진' },
        decided_by: null,
        decided_at: null,
        decision_note: null,
        granted_level: null,
        created_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 30 * 86_400_000).toISOString(),
      }
      state.requests.push(row)
      return route.fulfill({ status: 201, json: row })
    }
    const box = url.searchParams.get('box') ?? 'inbox'
    const items =
      as === 'manager' && box === 'inbox'
        ? state.requests.filter((item) => item.status === 'pending')
        : as === 'member' && box === 'outbox'
          ? state.requests
          : []
    return route.fulfill({ json: { items, next_cursor: null, has_more: false } })
  }

  return { state, install }
}

test.describe('Access request → approval → immediate use (E2E #7)', () => {
  test.skip(process.env.E2E_ORG_AUTHZ !== '1', 'Org-authz role fixtures (plan 16.5) pending')

  test('a member requests edit access and uses the skill right after approval', async ({
    page,
    browser,
  }) => {
    const server = accessServer()
    await server.install(page, 'member')

    // 1. The member hits the 403 and sends a request from the permission state.
    await page.goto(`/skills/${SKILL_ID}/settings`)
    await expect(page.getByRole('heading', { name: '권한이 필요해요' })).toBeVisible()
    await page.getByRole('button', { name: '권한 요청' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('radio', { name: /편집/ }).check()
    await dialog.getByLabel('필요한 이유 (선택)').fill('분기 리포트 양식을 수정해야 해요.')
    await dialog.getByRole('button', { name: '요청 보내기' }).click()
    await expect(page.getByText('요청을 보냈어요. 승인되면 바로 사용할 수 있어요.')).toBeVisible()
    expect(server.state.requests).toHaveLength(1)

    // 2. The skill's manager approves it from the S18 inbox.
    const managerContext = await browser.newContext({ storageState: undefined })
    try {
      await server.install(managerContext, 'manager')
      const manager = await managerContext.newPage()
      await manager.goto('/settings/organization/requests')
      const row = manager.getByRole('listitem').filter({ hasText: '정우진 → 영업 리포트 · 편집' })
      await expect(row).toBeVisible()
      await row.getByRole('button', { name: '승인' }).click()
      await expect(manager.getByText('요청을 승인했어요')).toBeVisible()
    } finally {
      await managerContext.close()
    }

    // 3. Immediate use: the member's reload opens the skill instead of the 403 state.
    await page.reload()
    await expect(page.getByRole('heading', { name: '권한이 필요해요' })).toHaveCount(0)
    expect(server.state.granted).toBe(true)
  })
})
