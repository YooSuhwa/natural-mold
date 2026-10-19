import type { APIRequestContext } from '@playwright/test'

import { isRecord, type CsrfHeaders } from '../fixtures'
import { call, log, str, tenantVersion, orgVersion, type Json } from './org-authz-api'
import { seedResources } from './org-authz-seed-resources'

/**
 * Real org-authz data for the capture tour and the axe scan, seeded over the API as the
 * E2E super user (company + org admin of the E2E scope).
 */

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

export interface Seed {
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

export interface MemberSessions {
  members: Record<string, string>
  memberCsrf: Record<string, CsrfHeaders>
  memberContexts: Record<string, APIRequestContext>
  pendingTokens: Record<string, string>
}

type PlaywrightRequests = { request: { newContext: () => Promise<APIRequestContext> } }

async function seedPlatformTenants(request: APIRequestContext, csrf: CsrfHeaders) {
  const ids: string[] = []
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
    if (id) ids.push(id)
  }
  const suspendId = ids[2]
  if (suspendId) {
    const detail = await call(request, 'GET', `/api/admin/tenants/${suspendId}`, {})
    const updated = (str(detail.body, 'updated_at') ?? '').replace(/Z$/, '+00:00')
    await call(request, 'POST', `/api/admin/tenants/${suspendId}/suspend`, {
      ...csrf,
      'If-Match': detail.etag ?? `"${updated}"`,
    })
  }
  return ids
}

/** Members via org invitations (accepted by registering); pending ones keep their token. */
async function seedMembers(
  playwright: PlaywrightRequests,
  request: APIRequestContext,
  csrf: CsrfHeaders,
  orgId: string,
): Promise<MemberSessions> {
  const sessions: MemberSessions = {
    members: {},
    memberCsrf: {},
    memberContexts: {},
    pendingTokens: {},
  }
  for (const persona of PERSONAS) {
    const inv = await call(request, 'POST', `/api/orgs/${orgId}/invitations`, csrf, {
      email: persona.email,
      role: persona.role,
    })
    const token = str(inv.body, 'token')
    if (!token) continue
    if (!persona.accept) {
      sessions.pendingTokens[persona.key] = token
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
      const credentials = { email: persona.email, password: PASSWORD }
      body = (await call(ctx, 'POST', '/api/auth/login', {}, credentials)).body
    }
    const userId = isRecord(body) ? (str(body.user, 'id') ?? str(body, 'id')) : null
    const csrfToken = str(body, 'csrf_token')
    if (csrfToken) sessions.memberCsrf[persona.key] = { 'X-CSRF-Token': csrfToken }
    sessions.memberContexts[persona.key] = ctx
    const meRes = await call(ctx, 'GET', '/api/auth/me', {})
    const id = str(meRes.body, 'id') ?? userId
    if (id) sessions.members[persona.key] = id
    const headers = sessions.memberCsrf[persona.key] ?? {}
    await call(ctx, 'POST', '/api/me/active-org', headers, { org_id: orgId })
  }
  return sessions
}

export async function seed(
  playwright: PlaywrightRequests,
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
  const tenantHeaders = { ...csrf, 'If-Match': await tenantVersion(request, tenantId) }
  await call(request, 'PATCH', `/api/tenants/${tenantId}`, tenantHeaders, { name: '누리테크' })
  const orgHeaders = { ...csrf, 'If-Match': await orgVersion(request, orgId) }
  await call(request, 'PATCH', `/api/orgs/${orgId}`, orgHeaders, { name: '제품개발본부' })

  // U-01 platform tenants exist only in multi_tenant installs; elsewhere the tour stubs them.
  const platformTenantIds = await seedPlatformTenants(request, csrf)
  const sessions = await seedMembers(playwright, request, csrf, orgId)
  log(`members=${JSON.stringify(sessions.members)} pending=${Object.keys(sessions.pendingTokens)}`)
  const resources = await seedResources(request, csrf, { orgId, tenantId, meId, sessions })

  for (const ctx of Object.values(sessions.memberContexts)) await ctx.dispose()
  return {
    orgId,
    tenantId,
    meId,
    members: sessions.members,
    pendingTokens: sessions.pendingTokens,
    ...resources,
    platformTenantIds,
  }
}
