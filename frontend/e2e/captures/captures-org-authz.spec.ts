import fs from 'node:fs/promises'
import path from 'node:path'
import type { APIRequestContext, Page, Route } from '@playwright/test'

import { API_BASE, isRecord, loginApi, test, type CsrfHeaders } from '../fixtures'
import { readinessServer } from '../helpers/readiness-server'
import { scriptedModelId, settle } from './_capture-helpers'

/**
 * Org-authz capture tour (U-01..U-04, E-04, G-04/G-05, H-01..H-08). Gated by
 * E2E_CAPTURE_TOUR=1. Needs DEPLOYMENT_MODE=multi_tenant (platform tenant screens) and
 * E2E_SEED_USER_ENABLED=true (seeded super user = company + org admin of the E2E scope).
 *
 * Real data is seeded over the API. States that only exist in AUTHZ_MODE=enforce
 * (403 permission_required, runner views, readiness/auth cards, access-request inbox) are
 * played over `page.route` the same way the opt-in org-authz specs do.
 */

const OUT_DIR = path.join('..', 'output', 'captures', 'org-authz')
const VIEWPORT = { width: 1440, height: 900 } as const
const PASSWORD = 'correct horse battery staple 42'

interface Persona {
  key: string
  name: string
  email: string
  role: 'member' | 'builder' | 'admin' | 'auditor'
  accept: boolean
}

const PERSONAS: readonly Persona[] = [
  {
    key: 'm1',
    name: '박서준',
    email: 'seojun.park@nuritech.example',
    role: 'member',
    accept: true,
  },
  { key: 'm2', name: '이하은', email: 'haeun.lee@nuritech.example', role: 'builder', accept: true },
  {
    key: 'm3',
    name: '정우진',
    email: 'woojin.jung@nuritech.example',
    role: 'auditor',
    accept: true,
  },
  { key: 'p1', name: '최유나', email: 'yuna.choi@nuritech.example', role: 'member', accept: false },
  {
    key: 'p2',
    name: '한도윤',
    email: 'doyun.han@nuritech.example',
    role: 'builder',
    accept: false,
  },
]

type Json = Record<string, unknown>

function log(message: string): void {
  console.log(`[org-authz-capture] ${message}`)
}

async function call(
  request: APIRequestContext,
  method: string,
  url: string,
  headers: Record<string, string>,
  data?: unknown,
): Promise<{ status: number; body: unknown; etag: string | undefined }> {
  const response = await request.fetch(`${API_BASE}${url}`, {
    method,
    headers,
    data: data === undefined ? undefined : data,
  })
  const text = await response.text()
  let body: unknown = text
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    // keep text
  }
  if (!response.ok()) log(`${method} ${url} -> ${response.status()} ${text.slice(0, 300)}`)
  return { status: response.status(), body, etag: response.headers()['etag'] }
}

function str(value: unknown, key: string): string | null {
  return isRecord(value) && typeof value[key] === 'string' ? (value[key] as string) : null
}

async function orgVersion(request: APIRequestContext, orgId: string): Promise<string> {
  const res = await call(request, 'GET', `/api/orgs/${orgId}`, {})
  if (res.etag) return res.etag
  return `"${str(res.body, 'updated_at') ?? ''}"`
}

async function tenantVersion(request: APIRequestContext, tenantId: string): Promise<string> {
  const res = await call(request, 'GET', `/api/tenants/${tenantId}`, {})
  if (res.etag) return res.etag
  const updated = str(res.body, 'updated_at') ?? ''
  return `"${updated.replace(/Z$/, '+00:00')}"`
}

interface Seed {
  orgId: string
  tenantId: string
  meId: string
  members: Record<string, string>
  pendingTokens: Record<string, string>
  agentId: string | null
  member1AgentId: string | null
  marketplaceItemId: string | null
  platformTenantIds: string[]
}

async function seed(
  playwright: { request: { newContext: () => Promise<APIRequestContext> } },
  request: APIRequestContext,
  csrf: CsrfHeaders,
): Promise<Seed> {
  const me = await call(request, 'GET', '/api/auth/me', {})
  const meId = str(me.body, 'id') ?? ''
  await call(request, 'PATCH', '/api/auth/me/profile', csrf, { display_name: '김지원' })

  const orgs = await call(request, 'GET', '/api/me/organizations', {})
  const list = Array.isArray(orgs.body) ? (orgs.body as Json[]) : []
  const active = list.find((row) => row.is_active === true) ?? list[0]
  const orgId = str(active, 'id') ?? ''
  const tenantId = str(active, 'tenant_id') ?? ''
  log(`org=${orgId} tenant=${tenantId} me=${meId}`)

  // Korean company / organization names instead of the "E2E Company" fixture names.
  await call(
    request,
    'PATCH',
    `/api/tenants/${tenantId}`,
    {
      ...csrf,
      'If-Match': await tenantVersion(request, tenantId),
    },
    { name: '누리테크' },
  )
  await call(
    request,
    'PATCH',
    `/api/orgs/${orgId}`,
    {
      ...csrf,
      'If-Match': await orgVersion(request, orgId),
    },
    { name: '제품개발본부' },
  )

  // ── U-01: platform tenants ───────────────────────────────────────────────
  const platformTenantIds: string[] = []
  const tenants = [
    ['한빛물산', 'hanbit', 'admin@hanbit.example', '경영지원팀', 50, 500, 5],
    ['세종에듀', 'sejong-edu', 'it@sejong-edu.example', '교육운영팀', 200, 1500, 10],
    ['바다물류', 'bada-logis', 'ops@bada-logis.example', '물류센터', 30, null, 3],
  ] as const
  for (const [name, slug, email, orgName, members, spend, runs] of tenants) {
    const res = await call(request, 'POST', '/api/admin/tenants', csrf, {
      name,
      slug,
      first_admin_email: email,
      first_organization_name: orgName,
      member_limit: members,
      monthly_spend_limit: spend,
      concurrent_run_limit: runs,
      allow_system_models: true,
    })
    const id = isRecord(res.body) ? str(res.body.tenant, 'id') : null
    if (id) platformTenantIds.push(id)
  }
  const suspendId = platformTenantIds[2]
  if (suspendId) {
    const detail = await call(request, 'GET', `/api/admin/tenants/${suspendId}`, {})
    const updated = (str(detail.body, 'updated_at') ?? '').replace(/Z$/, '+00:00')
    await call(request, 'POST', `/api/admin/tenants/${suspendId}/suspend`, {
      ...csrf,
      'If-Match': detail.etag ?? `"${updated}"`,
    })
  }

  // ── Members via org invitations (accepted by registering) ────────────────
  const members: Record<string, string> = {}
  const memberCsrf: Record<string, CsrfHeaders> = {}
  const memberContexts: Record<string, APIRequestContext> = {}
  const pendingTokens: Record<string, string> = {}
  for (const persona of PERSONAS) {
    const inv = await call(request, 'POST', `/api/orgs/${orgId}/invitations`, csrf, {
      email: persona.email,
      role: persona.role,
    })
    const token = str(inv.body, 'token')
    if (!token) continue
    if (!persona.accept) {
      pendingTokens[persona.key] = token
      continue
    }
    const ctx = await playwright.request.newContext()
    const reg = await call(
      ctx,
      'POST',
      '/api/auth/register',
      {},
      {
        email: persona.email,
        password: PASSWORD,
        display_name: persona.name,
        invitation_token: token,
      },
    )
    let body = reg.body
    if (reg.status >= 400) {
      body = (
        await call(
          ctx,
          'POST',
          '/api/auth/login',
          {},
          {
            email: persona.email,
            password: PASSWORD,
          },
        )
      ).body
    }
    const userId = isRecord(body) ? (str(body.user, 'id') ?? str(body, 'id')) : null
    const csrfToken = str(body, 'csrf_token')
    if (csrfToken) memberCsrf[persona.key] = { 'X-CSRF-Token': csrfToken }
    memberContexts[persona.key] = ctx
    const meRes = await call(ctx, 'GET', '/api/auth/me', {})
    const id = str(meRes.body, 'id') ?? userId
    if (id) members[persona.key] = id
    await call(ctx, 'POST', '/api/me/active-org', memberCsrf[persona.key] ?? {}, { org_id: orgId })
  }
  log(`members=${JSON.stringify(members)} pending=${Object.keys(pendingTokens).join(',')}`)

  // Company invitation (S06) and a second organization (archive dialog target).
  await call(
    request,
    'POST',
    `/api/tenants/${tenantId}/invitations`,
    {
      ...csrf,
      'If-Match': await tenantVersion(request, tenantId),
    },
    { email: 'audit@nuritech.example', roles: ['auditor'] },
  )
  await call(request, 'POST', `/api/tenants/${tenantId}/organizations`, csrf, {
    name: '마케팅본부',
    default_member_role: 'member',
  })
  await call(request, 'POST', `/api/tenants/${tenantId}/organizations`, csrf, {
    name: '고객지원센터',
    default_member_role: 'builder',
  })

  // Groups and roles.
  const group = await call(
    request,
    'POST',
    `/api/orgs/${orgId}/groups`,
    {
      ...csrf,
      'If-Match': await orgVersion(request, orgId),
    },
    { name: '플랫폼팀', description: '사내 AI 플랫폼 운영 담당' },
  )
  const groupId = str(group.body, 'id')
  await call(
    request,
    'POST',
    `/api/orgs/${orgId}/groups`,
    {
      ...csrf,
      'If-Match': await orgVersion(request, orgId),
    },
    { name: '데이터분석팀', description: '리포트·대시보드 에이전트 사용' },
  )
  if (groupId) {
    for (const key of ['m1', 'm2']) {
      if (!members[key]) continue
      await call(
        request,
        'POST',
        `/api/orgs/${orgId}/groups/${groupId}/members`,
        {
          ...csrf,
          'If-Match': await orgVersion(request, orgId),
        },
        { user_id: members[key] },
      )
    }
  }
  if (members.m2) {
    await call(
      request,
      'PUT',
      `/api/orgs/${orgId}/roles`,
      {
        ...csrf,
        'If-Match': await orgVersion(request, orgId),
      },
      { principal: { type: 'user', id: members.m2 }, roles: ['builder'] },
    )
  }
  if (members.m3) {
    await call(
      request,
      'PUT',
      `/api/orgs/${orgId}/roles`,
      {
        ...csrf,
        'If-Match': await orgVersion(request, orgId),
      },
      { principal: { type: 'user', id: members.m3 }, roles: ['auditor'] },
    )
  }
  if (groupId) {
    await call(
      request,
      'PUT',
      `/api/orgs/${orgId}/capabilities`,
      {
        ...csrf,
        'If-Match': await orgVersion(request, orgId),
      },
      {
        principal: { type: 'group', id: groupId },
        capabilities: ['cap_create_agent', 'cap_create_skill'],
      },
    )
  }
  await call(
    request,
    'PUT',
    `/api/orgs/${orgId}/limits`,
    {
      ...csrf,
      'If-Match': await orgVersion(request, orgId),
    },
    { default_member_limit: '50' },
  )

  // Agents: one owned by me (share dialog), one owned by 박서준 (removal wizard).
  const modelId = await scriptedModelId(request)
  const agent = await call(request, 'POST', '/api/agents', csrf, {
    name: '사내 IT 헬프데스크',
    description: '비밀번호 초기화·VPN·장비 요청 등 사내 IT 문의를 처리',
    system_prompt: '당신은 사내 IT 헬프데스크 상담원입니다.',
    model_id: modelId,
  })
  const agentId = str(agent.body, 'id')
  let member1AgentId: string | null = null
  if (memberContexts.m1) {
    const ctx = memberContexts.m1
    const h = memberCsrf.m1 ?? {}
    const a1 = await call(ctx, 'POST', '/api/agents', h, {
      name: '주간 영업 리포트 봇',
      description: '매주 월요일 영업 실적을 요약',
      system_prompt: '영업 실적을 요약합니다.',
      model_id: modelId,
    })
    member1AgentId = str(a1.body, 'id')
    await call(ctx, 'POST', '/api/agents', h, {
      name: '견적서 초안 도우미',
      description: '고객 요청을 받아 견적서 초안을 작성',
      system_prompt: '견적서 초안을 작성합니다.',
      model_id: modelId,
    })
    // 박서준 shares his agent with me → grant.received notification (H-02).
    if (member1AgentId && meId) {
      await call(ctx, 'POST', `/api/resources/agent/${member1AgentId}/grants`, h, {
        principal: { type: 'user', id: meId },
        level: 'viewer',
      })
    }
  }
  if (agentId) {
    if (members.m2) {
      await call(request, 'POST', `/api/resources/agent/${agentId}/grants`, csrf, {
        principal: { type: 'user', id: members.m2 },
        level: 'editor',
      })
    }
    if (groupId) {
      await call(request, 'POST', `/api/resources/agent/${agentId}/grants`, csrf, {
        principal: { type: 'group', id: groupId },
        level: 'consumer',
      })
    }
  }

  // Company models / shared credential / SSO (S07, S30).
  const credential = await call(request, 'POST', `/api/tenants/${tenantId}/credentials`, csrf, {
    definition_key: 'openai',
    name: '누리테크 OpenAI 공용 키',
    data: { api_key: 'sk-capture-placeholder-0000' },
    allowed_org_ids: [orgId],
  })
  await call(request, 'POST', `/api/tenants/${tenantId}/models`, csrf, {
    provider: 'openai',
    model_name: 'gpt-4.1-mini',
    display_name: '사내 GPT-4.1 mini',
    default_credential_id: str(credential.body, 'id'),
    allowed_org_ids: [orgId],
  })
  await call(request, 'POST', `/api/tenants/${tenantId}/domains`, csrf, {
    domain: 'nuritech.example',
  })
  await call(request, 'POST', `/api/tenants/${tenantId}/sso-connections`, csrf, {
    protocol: 'oidc',
    issuer: 'https://login.nuritech.example',
    client_id: 'moldy-portal',
    client_secret: 'capture-placeholder-secret',
    jit_enabled: true,
    trust_email_claim: false,
    default_role: 'member',
  })

  // Restricted marketplace item I manage (marketplace share dialog).
  let marketplaceItemId: string | null = null
  if (agentId) {
    const item = await call(request, 'POST', `/api/marketplace/items/from-agent/${agentId}`, csrf, {
      visibility: 'restricted',
      name: '사내 IT 헬프데스크',
      description: '사내 IT 문의 응대 에이전트 (제한 공개)',
      acl_user_ids: Object.values(members).slice(0, 2),
    })
    marketplaceItemId = str(item.body, 'id')
  }

  for (const ctx of Object.values(memberContexts)) await ctx.dispose()
  return {
    orgId,
    tenantId,
    meId,
    members,
    pendingTokens,
    agentId,
    member1AgentId,
    marketplaceItemId,
    platformTenantIds,
  }
}

const FAKE_SKILL_ID = '00000000-0000-4000-8000-0000000000a1'
const AUTH_CREDENTIAL_ID = '33333333-3333-4333-8333-333333333333'

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

test.describe('Org-authz screens — capture tour', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')
  test.use({ actionTimeout: 15_000 })

  test('captures every org-authz screen', async ({ page, request, playwright }) => {
    test.setTimeout(1_500_000)
    await fs.mkdir(OUT_DIR, { recursive: true })
    await page.setViewportSize(VIEWPORT)
    const failed: string[] = []
    const done: string[] = []

    async function shot(file: string, fullPage = false): Promise<void> {
      if (!fullPage) {
        await page.screenshot({ path: path.join(OUT_DIR, file) })
        done.push(file)
        return
      }
      // The app shell scrolls an inner container, so `fullPage` alone stays at the
      // viewport: grow the viewport by the inner overflow, capture, then restore.
      const overflow = await page.evaluate(() => {
        let extra = 0
        for (const el of Array.from(document.querySelectorAll<HTMLElement>('*'))) {
          const style = getComputedStyle(el)
          if (!/(auto|scroll)/.test(style.overflowY)) continue
          if (el.clientHeight < 300) continue
          extra = Math.max(extra, el.scrollHeight - el.clientHeight)
        }
        return extra
      })
      const height = Math.min(VIEWPORT.height + overflow, 4_000)
      if (height > VIEWPORT.height) {
        await page.setViewportSize({ width: VIEWPORT.width, height })
        await page.waitForTimeout(600)
      }
      await page.screenshot({ path: path.join(OUT_DIR, file), fullPage: true })
      if (height > VIEWPORT.height) await page.setViewportSize(VIEWPORT)
      done.push(file)
    }
    async function visit(url: string): Promise<void> {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120_000 })
      await settle(page, 1_200)
    }
    async function step(name: string, fn: () => Promise<void>): Promise<void> {
      try {
        await fn()
      } catch (error) {
        failed.push(`${name}: ${String(error).split('\n')[0]}`)
        await page.screenshot({ path: path.join(OUT_DIR, `zz-debug-${name}.png`) }).catch(() => {})
        log(`STEP FAILED ${name}: ${String(error).slice(0, 400)}`)
      }
      await page.keyboard.press('Escape').catch(() => {})
      await page.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {})
    }
    const dialog = () => page.locator('[role="dialog"], [role="alertdialog"]').last()

    const csrf = await loginApi(request)
    const s = await seed(playwright, request, csrf)

    // ── U-01 platform operator: company list / detail / risky confirm ─────────
    await step('u01-list', async () => {
      await visit('/settings/admin/tenants')
      await page.getByText('한빛물산').first().waitFor({ timeout: 30_000 })
      await shot('01-u01-tenant-list.png', true)
    })
    await step('u01-risky', async () => {
      await page.getByRole('row').filter({ hasText: '한빛물산' }).getByRole('button').last().click()
      await page.getByRole('menuitem', { name: '정지' }).click()
      await dialog().waitFor()
      await page.waitForTimeout(500)
      await shot('02-u01-tenant-suspend-confirm.png')
    })
    await step('u01-risky-delete', async () => {
      await visit('/settings/admin/tenants')
      await page.getByRole('row').filter({ hasText: '세종에듀' }).getByRole('button').last().click()
      await page.getByRole('menuitem', { name: '삭제 예약' }).click()
      await dialog().waitFor()
      await page.waitForTimeout(500)
      await shot('03-u01-tenant-schedule-deletion-confirm.png')
    })
    await step('u01-detail', async () => {
      const id = s.platformTenantIds[0]
      if (!id) throw new Error('no platform tenant seeded')
      await visit(`/settings/admin/tenants/${id}`)
      await page.waitForTimeout(800)
      await shot('04-u01-tenant-detail.png', true)
    })

    // ── U-02 company settings ────────────────────────────────────────────────
    await step('u02-overview', async () => {
      await visit('/settings/company')
      await page.getByText('마케팅본부').first().waitFor({ timeout: 30_000 })
      await shot('05-u02-company-overview.png', true)
    })
    await step('u04-org-archive', async () => {
      await page.getByRole('button', { name: '마케팅본부 보관' }).click()
      await dialog().waitFor()
      await page.waitForTimeout(500)
      await shot('06-u04-org-archive-dialog.png')
    })
    await step('u02-policies', async () => {
      await visit('/settings/company/policies')
      await shot('07-u02-company-policies.png', true)
    })
    await step('u02-members', async () => {
      await visit('/settings/company/members')
      await page.getByText('박서준').first().waitFor({ timeout: 30_000 })
      await shot('08-u02-company-members.png', true)
    })
    await step('u02-invite', async () => {
      await page.getByRole('button', { name: '회사에 초대' }).first().click()
      await dialog().waitFor()
      await page.waitForTimeout(400)
      await shot('09-u02-company-invite-dialog.png')
    })
    const companyTabs: ReadonlyArray<readonly [string, string]> = [
      ['모델', '10-u02-company-models.png'],
      ['공용 자격증명', '11-u02-company-shared-credentials.png'],
      ['서비스 LLM', '12-u02-company-service-llm.png'],
      ['회사 카탈로그', '13-u02-company-catalog.png'],
    ]
    await step('u02-models', async () => {
      await visit('/settings/company/models')
      for (const [tab, file] of companyTabs) {
        await page.getByRole('tab', { name: tab, exact: true }).click()
        await settle(page, 800)
        await shot(file, true)
      }
    })

    // ── H-08 company SSO ─────────────────────────────────────────────────────
    await step('h08-sso', async () => {
      await visit('/settings/company/sso')
      await page.getByText('nuritech.example').first().waitFor({ timeout: 20_000 })
      await shot('14-h08-company-sso.png', true)
    })

    // ── U-03 organization settings ───────────────────────────────────────────
    await step('u03-settings', async () => {
      await visit('/settings/organization')
      await page.getByText('사용 한도').first().waitFor({ timeout: 20_000 })
      await shot('15-u03-org-settings.png', true)
    })
    await step('u03-catalog', async () => {
      await visit('/settings/organization/catalog')
      await settle(page, 800)
      await shot('16-u03-org-catalog.png', true)
      const toggle = page.getByRole('switch', { checked: true }).first()
      await toggle.click()
      await dialog().waitFor()
      await page.waitForTimeout(800)
      await shot('17-u03-org-catalog-disable-confirm.png')
    })
    await step('u03-members', async () => {
      await visit('/settings/organization/members')
      await page.getByText('박서준').first().waitFor({ timeout: 20_000 })
      await shot('18-u03-org-members.png', true)
    })
    await step('u03-invite', async () => {
      await page.getByRole('button', { name: '구성원 초대' }).first().click()
      await dialog().waitFor()
      await dialog()
        .getByRole('textbox')
        .first()
        .fill('minji.kim@nuritech.example, jihoon.oh@nuritech.example')
      await page.waitForTimeout(400)
      await shot('20-u03-org-invite-dialog.png')
      await dialog()
        .getByRole('button', { name: /초대 보내기/ })
        .click()
      await dialog().getByText('초대 링크를 만들었어요').first().waitFor({ timeout: 15_000 })
      await page.waitForTimeout(400)
      await shot('21-u03-org-invite-links.png')
    })
    await step('h06-member-limit', async () => {
      await visit('/settings/organization/members')
      const row = page.getByRole('row').filter({ hasText: '이하은' })
      await row.getByRole('button', { name: '월 한도' }).click()
      await dialog().waitFor()
      await page.waitForTimeout(800)
      await shot('22-h06-member-monthly-limit-dialog.png')
    })
    await step('u03-roles', async () => {
      await visit('/settings/organization/roles')
      await settle(page, 800)
      await shot('23-u03-org-roles.png', true)
    })
    await step('u03-groups', async () => {
      await visit('/settings/organization/groups')
      await page.getByText('플랫폼팀').first().waitFor({ timeout: 20_000 })
      await page
        .getByText('플랫폼팀', { exact: true })
        .locator('xpath=ancestor::*[.//button][1]')
        .getByRole('button', { name: '구성원', exact: true })
        .click()
        .catch(() => {})
      await settle(page, 600)
      await shot('24-u03-org-groups.png', true)
    })
    await step('u03-requests-real', async () => {
      await visit('/settings/organization/requests')
      await shot('25-u03-org-requests-empty.png', true)
    })

    // ── U-04 member removal wizard + transfer ownership ───────────────────────
    await step('u04-removal', async () => {
      await visit('/settings/organization/members')
      const row = page.getByRole('row').filter({ hasText: '박서준' })
      await row.getByRole('button', { name: '제거' }).click()
      await dialog().waitFor()
      await dialog().getByText('주간 영업 리포트 봇').first().waitFor({ timeout: 20_000 })
      await page.waitForTimeout(500)
      await shot('26-u04-removal-step1-resources.png')
      const picker = dialog().getByRole('combobox', { name: '모든 이전 항목의 새 소유자' })
      await picker.fill('김지')
      await page.getByRole('option').first().click({ timeout: 15_000 })
      await page.waitForTimeout(400)
      await shot('27-u04-removal-step1-owner-selected.png')
      await dialog().getByRole('button', { name: '다음' }).click()
      await page.waitForTimeout(600)
      await shot('28-u04-removal-step2-effects.png')
      await dialog().getByRole('button', { name: '다음' }).click()
      await dialog().getByRole('textbox').last().fill('부서 이동 (영업본부 → 마케팅본부)')
      await page.waitForTimeout(400)
      await shot('29-u04-removal-step3-confirm.png')
      await dialog().getByRole('button', { name: '이전', exact: true }).click()
      await dialog().getByRole('button', { name: '이전', exact: true }).click()
      await page.waitForTimeout(300)
      // "Transfer now" is offered only when the caller can govern the resource;
      // legacy mode hides it by design, so shot 30 exists only in fga mode.
      const transferNow = dialog().getByRole('button', { name: /지금 바로 이전/ })
      if ((await transferNow.count()) > 0) {
        await transferNow.first().click()
        await page.waitForTimeout(800)
        await shot('30-u04-transfer-ownership-dialog.png')
      }
    })

    // ── E-04 resource share dialog (agent) + marketplace share ───────────────
    await step('e04-share', async () => {
      if (!s.agentId) throw new Error('no agent')
      await visit(`/agents/${s.agentId}/settings`)
      await page.getByRole('button', { name: '사내 IT 헬프데스크 공유 설정 열기' }).click()
      await dialog().waitFor()
      await settle(page, 1_000)
      await shot('31-e04-agent-share-dialog.png')
      await dialog().getByRole('button', { name: '소유권 이전' }).first().click()
      await page.waitForTimeout(500)
      await shot('32-e04-agent-share-transfer-section.png')
    })
    await step('e04-marketplace-share', async () => {
      if (!s.marketplaceItemId) throw new Error('no marketplace item')
      await visit(`/marketplace/${s.marketplaceItemId}`)
      await shot('33-e04-marketplace-item-restricted.png', true)
      await page.getByRole('button', { name: '대상 관리' }).first().click()
      await dialog().waitFor()
      await settle(page, 800)
      await shot('34-e04-marketplace-share-dialog.png')
    })

    // ── G-04 readiness badge / check panel / binding dialog (route-played) ───
    await step('g04-badge', async () => {
      await page.route('**/api/agents/readiness-summary', async (route: Route) => {
        const body = route.request().postDataJSON() as { agent_ids?: string[] } | null
        const ids = body?.agent_ids ?? []
        const json: Record<string, unknown> = {}
        ids.forEach((id, index) => {
          json[id] =
            index % 3 === 0
              ? { overall: 'needs_connection', counts: { connect_by_me: 1 } }
              : index % 3 === 1
                ? { overall: 'limited', counts: { no_permission: 2 } }
                : { overall: 'blocked', counts: { owner_must_connect: 1 } }
        })
        await route.fulfill({ json })
      })
      await visit('/')
      await page.waitForTimeout(1_500)
      await shot('35-g04-readiness-badges-dashboard.png')
    })
    await step('g04-panel', async () => {
      if (!s.agentId) throw new Error('no agent')
      const server = readinessServer()
      await server.install(page, s.agentId)
      await visit(`/agents/${s.agentId}/conversations/new`)
      await page.getByText('필수 항목 1개 연결이 필요해요').waitFor({ timeout: 60_000 })
      await page.waitForTimeout(500)
      await shot('36-g04-readiness-collapsed-card.png')
      await page.getByRole('button', { name: '점검 보기' }).click()
      await dialog().waitFor()
      await page.waitForTimeout(600)
      await shot('37-g04-readiness-check-panel.png')
      await dialog().getByRole('button', { name: '연결하기' }).click()
      await page.waitForTimeout(800)
      await shot('38-g04-credential-binding-dialog.png')
    })

    // ── G-05 auth_required card (route-played interrupt) ─────────────────────
    await step('g05-auth-card', async () => {
      if (!s.agentId) throw new Error('no agent')
      const convo = await call(request, 'POST', `/api/agents/${s.agentId}/conversations`, csrf, {
        title: 'Slack 공지 올리기',
      })
      const conversationId = str(convo.body, 'id')
      if (!conversationId) throw new Error('conversation create failed')
      const interrupt = {
        id: 'intr-auth-oauth',
        value: {
          type: 'auth_required',
          reason: 'expired',
          attempt: 1,
          allowed_decisions: ['respond'],
          items: [
            {
              resource_type: 'mcp_server',
              resource_name: 'Slack',
              tool_names: ['post_message'],
              credential_id: AUTH_CREDENTIAL_ID,
              fixable_by: 'me',
              connect: { kind: 'oauth' },
            },
          ],
        },
      }
      await page.route(`**/langgraph/threads/${conversationId}/state**`, async (route) => {
        const real = await route.fetch()
        const body = (await real.json()) as Record<string, unknown>
        const tasks = [{ id: 'run-auth', name: 'interrupted', interrupts: [interrupt] }]
        await route.fulfill({ response: real, json: { ...body, tasks, interrupts: [interrupt] } })
      })
      await visit(`/agents/${s.agentId}/conversations/${conversationId}`)
      await page.getByText('연결이 필요합니다').first().waitFor({ timeout: 60_000 })
      await page.waitForTimeout(800)
      await shot('39-g05-auth-required-card.png')
    })

    // ── H-01 permission required + request dialog + inbox (route-played) ─────
    await step('h01-permission', async () => {
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
      await visit(`/skills/${FAKE_SKILL_ID}/settings`)
      await page.getByRole('heading', { name: '권한이 필요해요' }).waitFor({ timeout: 60_000 })
      await shot('40-h01-permission-required.png')
      await page.getByRole('button', { name: '권한 요청' }).click()
      await dialog().waitFor()
      await dialog()
        .getByRole('radio', { name: /편집/ })
        .check()
        .catch(() => {})
      await dialog().getByLabel('필요한 이유 (선택)').fill('분기 리포트 양식을 수정해야 해요.')
      await page.waitForTimeout(400)
      await shot('41-h01-request-access-dialog.png')
    })
    await step('h01-inbox', async () => {
      const rows = [
        accessRequestRow(1, '박서준', '영업 리포트', 'editor'),
        accessRequestRow(2, '이하은', '고객 문의 분류기', 'viewer'),
        accessRequestRow(3, '정우진', '사내 IT 헬프데스크', 'consumer'),
      ]
      await page.route('**/api/access-requests**', (route) => {
        const box = new URL(route.request().url()).searchParams.get('box') ?? 'inbox'
        return route.fulfill({
          json: { items: box === 'inbox' ? rows : [], next_cursor: null, has_more: false },
        })
      })
      await visit('/settings/organization/requests')
      await page.getByText('박서준').first().waitFor({ timeout: 20_000 })
      await shot('42-h01-access-request-inbox.png', true)
    })

    // ── H-02 notifications bell ──────────────────────────────────────────────
    await step('h02-bell-real', async () => {
      await visit('/')
      await page.getByRole('button', { name: /^알림/ }).first().click()
      await page.waitForTimeout(1_000)
      await shot('43-h02-notifications-real.png')
    })
    await step('h02-bell-rich', async () => {
      const now = Date.now()
      const iso = (minutes: number) => new Date(now - minutes * 60_000).toISOString()
      const items = [
        {
          id: 'n1',
          kind: 'access_request.created',
          org_id: s.orgId,
          target: { type: 'skill', id: FAKE_SKILL_ID, name: '영업 리포트', accessible: true },
          actor: { id: 'u1', display_name: '박서준' },
          payload: { level: 'editor' },
          link: '/settings/organization/requests',
          read_at: null,
          created_at: iso(5),
        },
        {
          id: 'n2',
          kind: 'grant.received',
          org_id: s.orgId,
          target: {
            type: 'agent',
            id: s.member1AgentId,
            name: '주간 영업 리포트 봇',
            accessible: true,
          },
          actor: { id: 'u1', display_name: '박서준' },
          payload: { level: 'viewer' },
          link: null,
          read_at: null,
          created_at: iso(42),
        },
        {
          id: 'n3',
          kind: 'trigger.blocked',
          org_id: s.orgId,
          target: { type: 'trigger', id: 't1', name: '매일 아침 브리핑', accessible: true },
          actor: null,
          payload: { reason: 'permission_revoked', trigger_name: '매일 아침 브리핑' },
          link: null,
          read_at: iso(100),
          created_at: iso(180),
        },
        {
          id: 'n4',
          kind: 'ownership.transferred',
          org_id: s.orgId,
          target: { type: 'agent', id: s.agentId, name: '사내 IT 헬프데스크', accessible: true },
          actor: { id: 'u2', display_name: '이하은' },
          payload: { role: 'new_owner' },
          link: null,
          read_at: iso(1_000),
          created_at: iso(1_440),
        },
      ]
      await page.route('**/api/notifications/unread-count', (route) =>
        route.fulfill({ json: { total: 2, by_org: { [s.orgId]: 2 } } }),
      )
      await page.route('**/api/notifications?**', (route) =>
        route.fulfill({ json: { items, next_cursor: null, has_more: false } }),
      )
      await visit('/')
      await page.getByRole('button', { name: /^알림/ }).first().click()
      await page.waitForTimeout(1_000)
      await shot('44-h02-notifications-bell-open.png')
    })

    // ── H-04 operator authz sync screen ──────────────────────────────────────
    await step('h04-authz', async () => {
      await visit('/settings/admin/authz')
      await page.waitForTimeout(800)
      await shot('45-h04-admin-authz.png', true)
    })

    // ── H-05 runner (read-only) agent settings notice (route-played) ─────────
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
      await visit(`/agents/${s.agentId}/settings`)
      await page.waitForTimeout(1_500)
      await shot(file)
    }
    await step('h05-runner', () =>
      agentWithPermissions(['can_run'], '46-h05-runner-settings-notice.png'),
    )
    await step('h05-viewer', () =>
      agentWithPermissions(
        ['can_run', 'can_view', 'can_view_config'],
        '47-h05-viewer-read-only-settings.png',
      ),
    )

    // ── /invite/[token] accept page ──────────────────────────────────────────
    await step('invite-valid', async () => {
      const token = s.pendingTokens.p1
      if (!token) throw new Error('no pending token')
      await visit(`/invite/${token}`)
      await page
        .getByRole('button', { name: /참여|수락|가입/ })
        .first()
        .waitFor({ timeout: 20_000 })
      await shot('48-invite-accept-signed-in.png')
      await page
        .getByRole('button', { name: /참여|수락|가입/ })
        .first()
        .click()
      await page.getByRole('alert').first().waitFor({ timeout: 15_000 })
      await page.waitForTimeout(400)
      await shot('49-invite-accept-email-mismatch.png')
    })
    await step('invite-invalid', async () => {
      await visit('/invite/ov1.invalid-capture-token')
      await page
        .getByRole('button', { name: /참여|수락|가입/ })
        .first()
        .click()
      await page.getByRole('alert').first().waitFor({ timeout: 15_000 })
      await page.waitForTimeout(400)
      await shot('50-invite-accept-invalid-token.png')
    })
    await step('invite-logged-out', async () => {
      const token = s.pendingTokens.p2
      if (!token) throw new Error('no pending token')
      const browser = page.context().browser()
      if (!browser) throw new Error('no browser for a fresh context')
      const ctx = await browser.newContext({
        baseURL: page.url().replace(/\/invite.*$/, ''),
        viewport: VIEWPORT,
        storageState: { cookies: [], origins: [] },
      })
      const anon = await ctx.newPage()
      try {
        await anon.goto(`/invite/${token}`, { waitUntil: 'domcontentloaded', timeout: 120_000 })
        await settle(anon, 1_200)
        await anon.screenshot({ path: path.join(OUT_DIR, '51-invite-accept-logged-out.png') })
        done.push('51-invite-accept-logged-out.png')
      } finally {
        await ctx.close()
      }
    })

    log(`captured ${done.length}: ${done.join(', ')}`)
    log(`failed ${failed.length}: ${JSON.stringify(failed, null, 1)}`)
  })
})
