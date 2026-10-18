import type { APIRequestContext } from '@playwright/test'

import type { CsrfHeaders } from '../fixtures'
import { scriptedModelId } from '../captures/_capture-helpers'
import { call, orgVersion, str, tenantVersion } from './org-authz-api'
import type { MemberSessions } from './org-authz-seed'

interface Scope {
  orgId: string
  tenantId: string
  meId: string
  sessions: MemberSessions
}

async function seedOrgStructure(request: APIRequestContext, csrf: CsrfHeaders, scope: Scope) {
  const { orgId, tenantId } = scope
  const { members } = scope.sessions
  const versioned = async () => ({ ...csrf, 'If-Match': await orgVersion(request, orgId) })

  // Company invitation (S06) and two more organizations (archive dialog target).
  await call(
    request,
    'POST',
    `/api/tenants/${tenantId}/invitations`,
    { ...csrf, 'If-Match': await tenantVersion(request, tenantId) },
    { email: 'audit@nuritech.example', roles: ['auditor'] },
  )
  for (const [name, role] of [
    ['마케팅본부', 'member'],
    ['고객지원센터', 'builder'],
  ] as const) {
    await call(request, 'POST', `/api/tenants/${tenantId}/organizations`, csrf, {
      name,
      default_member_role: role,
    })
  }

  const group = await call(request, 'POST', `/api/orgs/${orgId}/groups`, await versioned(), {
    name: '플랫폼팀',
    description: '사내 AI 플랫폼 운영 담당',
  })
  const groupId = str(group.body, 'id')
  await call(request, 'POST', `/api/orgs/${orgId}/groups`, await versioned(), {
    name: '데이터분석팀',
    description: '리포트·대시보드 에이전트 사용',
  })
  if (groupId) {
    for (const key of ['m1', 'm2']) {
      if (!members[key]) continue
      await call(
        request,
        'POST',
        `/api/orgs/${orgId}/groups/${groupId}/members`,
        await versioned(),
        {
          user_id: members[key],
        },
      )
    }
  }
  for (const [key, role] of [
    ['m2', 'builder'],
    ['m3', 'auditor'],
  ] as const) {
    if (!members[key]) continue
    await call(request, 'PUT', `/api/orgs/${orgId}/roles`, await versioned(), {
      principal: { type: 'user', id: members[key] },
      roles: [role],
    })
  }
  if (groupId) {
    await call(request, 'PUT', `/api/orgs/${orgId}/capabilities`, await versioned(), {
      principal: { type: 'group', id: groupId },
      capabilities: ['cap_create_agent', 'cap_create_skill'],
    })
  }
  await call(request, 'PUT', `/api/orgs/${orgId}/limits`, await versioned(), {
    default_member_limit: '50',
  })
  return groupId
}

/** Agents: one owned by me (share dialog), two owned by 박서준 (removal wizard). */
async function seedAgents(
  request: APIRequestContext,
  csrf: CsrfHeaders,
  scope: Scope,
  groupId: string | null,
) {
  const { members, memberContexts, memberCsrf } = scope.sessions
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
    if (member1AgentId && scope.meId) {
      await call(ctx, 'POST', `/api/resources/agent/${member1AgentId}/grants`, h, {
        principal: { type: 'user', id: scope.meId },
        level: 'viewer',
      })
    }
  }
  if (agentId) {
    const grants = [
      members.m2 ? { principal: { type: 'user', id: members.m2 }, level: 'editor' } : null,
      groupId ? { principal: { type: 'group', id: groupId }, level: 'consumer' } : null,
    ]
    for (const grant of grants) {
      if (grant) await call(request, 'POST', `/api/resources/agent/${agentId}/grants`, csrf, grant)
    }
  }
  return { agentId, member1AgentId }
}

/** Company models / shared credential / SSO (S07, S30). */
async function seedCompanyCatalog(request: APIRequestContext, csrf: CsrfHeaders, scope: Scope) {
  const { orgId, tenantId } = scope
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
}

export async function seedResources(request: APIRequestContext, csrf: CsrfHeaders, scope: Scope) {
  const groupId = await seedOrgStructure(request, csrf, scope)
  const { agentId, member1AgentId } = await seedAgents(request, csrf, scope, groupId)
  await seedCompanyCatalog(request, csrf, scope)

  // Restricted marketplace item I manage (marketplace share dialog).
  let marketplaceItemId: string | null = null
  if (agentId) {
    const item = await call(request, 'POST', `/api/marketplace/items/from-agent/${agentId}`, csrf, {
      visibility: 'restricted',
      name: '사내 IT 헬프데스크',
      description: '사내 IT 문의 응대 에이전트 (제한 공개)',
      acl_user_ids: Object.values(scope.sessions.members).slice(0, 2),
    })
    marketplaceItemId = str(item.body, 'id')
  }
  return { agentId, member1AgentId, marketplaceItemId }
}
