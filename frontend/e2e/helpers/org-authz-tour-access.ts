import { settle } from '../captures/_capture-helpers'
import { settleUi } from './org-authz-api'
import type { TourContext } from './org-authz-tour'

const FAKE_SKILL_ID = '00000000-0000-4000-8000-0000000000a1'
const INVITE_BUTTON = /참여|수락|가입/

function accessRequestRow(index: number, name: string, resource: string, level: string) {
  return {
    id: `req-${index}`,
    resource_type: 'skill',
    resource_id: FAKE_SKILL_ID,
    resource_name: resource,
    level,
    reason: index === 1 ? '분기 리포트 양식을 수정해야 해요.' : null,
    status: 'pending',
    requester: { id: `member-${index}`, display_name: name },
    decided_by: null,
    decided_at: null,
    decision_note: null,
    granted_level: null,
    created_at: new Date(Date.now() - index * 3_600_000).toISOString(),
    expires_at: new Date(Date.now() + 29 * 86_400_000).toISOString(),
  }
}

function notifications(orgId: string, agentId: string | null, member1AgentId: string | null) {
  const now = Date.now()
  const iso = (minutes: number) => new Date(now - minutes * 60_000).toISOString()
  const base = { org_id: orgId, link: null }
  return [
    {
      ...base,
      id: 'n1',
      kind: 'access_request.created',
      target: { type: 'skill', id: FAKE_SKILL_ID, name: '영업 리포트', accessible: true },
      actor: { id: 'u1', display_name: '박서준' },
      payload: { level: 'editor' },
      link: '/settings/organization/requests',
      read_at: null,
      created_at: iso(5),
    },
    {
      ...base,
      id: 'n2',
      kind: 'grant.received',
      target: { type: 'agent', id: member1AgentId, name: '주간 영업 리포트 봇', accessible: true },
      actor: { id: 'u1', display_name: '박서준' },
      payload: { level: 'viewer' },
      read_at: null,
      created_at: iso(42),
    },
    {
      ...base,
      id: 'n3',
      kind: 'trigger.blocked',
      target: { type: 'trigger', id: 't1', name: '매일 아침 브리핑', accessible: true },
      actor: null,
      payload: { reason: 'permission_revoked', trigger_name: '매일 아침 브리핑' },
      read_at: iso(100),
      created_at: iso(180),
    },
    {
      ...base,
      id: 'n4',
      kind: 'ownership.transferred',
      target: { type: 'agent', id: agentId, name: '사내 IT 헬프데스크', accessible: true },
      actor: { id: 'u2', display_name: '이하은' },
      payload: { role: 'new_owner' },
      read_at: iso(1_000),
      created_at: iso(1_440),
    },
  ]
}

/** H-01 access requests, H-02 notifications, H-04 authz sync, H-05 read-only, invite page. */
export async function accessSteps(t: TourContext): Promise<void> {
  const { page, seed: s, dialog } = t
  await t.step('h01-permission', async () => {
    await page.route(`**/api/skills/${FAKE_SKILL_ID}`, (route) =>
      route.fulfill({
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
    await t.visit(`/skills/${FAKE_SKILL_ID}/settings`)
    await page.getByRole('heading', { name: '권한이 필요해요' }).waitFor({ timeout: 60_000 })
    await t.screen('40-h01-permission-required.png')
    await page.getByRole('button', { name: '권한 요청' }).click()
    await dialog().waitFor()
    await dialog()
      .getByRole('radio', { name: /편집/ })
      .check()
      .catch(() => {})
    await dialog().getByLabel('필요한 이유 (선택)').fill('분기 리포트 양식을 수정해야 해요.')
    await settleUi(page)
    await t.screen('41-h01-request-access-dialog.png')
  })
  await t.step('h01-inbox', async () => {
    const rows = [
      accessRequestRow(1, '박서준', '영업 리포트', 'editor'),
      accessRequestRow(2, '이하은', '고객 문의 분류기', 'viewer'),
      accessRequestRow(3, '정우진', '사내 IT 헬프데스크', 'consumer'),
    ]
    await page.route('**/api/access-requests**', (route) => {
      const box = new URL(route.request().url()).searchParams.get('box') ?? 'inbox'
      const items = box === 'inbox' ? rows : []
      return route.fulfill({ json: { items, next_cursor: null, has_more: false } })
    })
    await t.visit('/settings/organization/requests')
    await page.getByText('박서준').first().waitFor({ timeout: 20_000 })
    await t.screen('42-h01-access-request-inbox.png', { fullPage: true })
  })
  await t.step('h02-bell-real', async () => {
    await t.visit('/')
    await page.getByRole('button', { name: /^알림/ }).first().click()
    await settleUi(page)
    await t.screen('43-h02-notifications-real.png')
  })
  await t.step('h02-bell-rich', async () => {
    const items = notifications(s.orgId, s.agentId, s.member1AgentId)
    await page.route('**/api/notifications/unread-count', (route) =>
      route.fulfill({ json: { total: 2, by_org: { [s.orgId]: 2 } } }),
    )
    await page.route('**/api/notifications?**', (route) =>
      route.fulfill({ json: { items, next_cursor: null, has_more: false } }),
    )
    await t.visit('/')
    await page.getByRole('button', { name: /^알림/ }).first().click()
    await settleUi(page)
    await t.screen('44-h02-notifications-bell-open.png')
  })
  await t.step('h04-authz', async () => {
    await t.visit('/settings/admin/authz')
    await settleUi(page)
    await t.screen('45-h04-admin-authz.png', { fullPage: true })
  })
  await readOnlySteps(t)
  await inviteSteps(t)
}

/** H-05 runner (read-only) agent settings notice (route-played). */
async function readOnlySteps(t: TourContext): Promise<void> {
  const { page, seed: s } = t
  async function agentWithPermissions(permissions: string[], file: string): Promise<void> {
    if (!s.agentId) throw new Error('no agent')
    const agentPath = `/api/agents/${s.agentId}`
    await page.route(
      (url) => url.pathname === agentPath,
      async (route) => {
        if (route.request().method() !== 'GET') return route.fallback()
        const real = await route.fetch()
        const body = (await real.json()) as Record<string, unknown>
        await route.fulfill({ response: real, json: { ...body, my_permissions: permissions } })
      },
    )
    await t.visit(`/agents/${s.agentId}/settings`)
    await settleUi(page)
    await t.screen(file)
  }
  await t.step('h05-runner', () =>
    agentWithPermissions(['can_run'], '46-h05-runner-settings-notice.png'),
  )
  await t.step('h05-viewer', () =>
    agentWithPermissions(
      ['can_run', 'can_view', 'can_view_config'],
      '47-h05-viewer-read-only-settings.png',
    ),
  )
}

/** /invite/[token] accept page: signed in (mismatch), invalid token, signed out. */
async function inviteSteps(t: TourContext): Promise<void> {
  const { page, seed: s } = t
  await t.step('invite-valid', async () => {
    const token = s.pendingTokens.p1
    if (!token) throw new Error('no pending token')
    await t.visit(`/invite/${token}`)
    await page.getByRole('button', { name: INVITE_BUTTON }).first().waitFor({ timeout: 20_000 })
    await t.screen('48-invite-accept-signed-in.png')
    await page.getByRole('button', { name: INVITE_BUTTON }).first().click()
    await page.getByRole('alert').first().waitFor({ timeout: 15_000 })
    await settleUi(page)
    await t.screen('49-invite-accept-email-mismatch.png')
  })
  await t.step('invite-invalid', async () => {
    await t.visit('/invite/ov1.invalid-capture-token')
    await page.getByRole('button', { name: INVITE_BUTTON }).first().click()
    await page.getByRole('alert').first().waitFor({ timeout: 15_000 })
    await settleUi(page)
    await t.screen('50-invite-accept-invalid-token.png')
  })
  await t.step('invite-logged-out', async () => {
    const token = s.pendingTokens.p2
    if (!token) throw new Error('no pending token')
    const browser = page.context().browser()
    if (!browser) throw new Error('no browser for a fresh context')
    const ctx = await browser.newContext({
      baseURL: page.url().replace(/\/invite.*$/, ''),
      viewport: page.viewportSize() ?? undefined,
      storageState: { cookies: [], origins: [] },
    })
    const anon = await ctx.newPage()
    try {
      await anon.goto(`/invite/${token}`, { waitUntil: 'domcontentloaded', timeout: 120_000 })
      await settle(anon, 1_200)
      await t.screen('51-invite-accept-logged-out.png', { page: anon })
    } finally {
      await ctx.close()
    }
  })
}
