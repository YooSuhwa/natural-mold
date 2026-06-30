import fs from 'node:fs/promises'
import path from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'
import {
  API_BASE,
  apiGetJson,
  apiPostJson,
  isRecord,
  type CsrfHeaders,
} from '../fixtures'

/**
 * Shared helpers for the full-app capture tour (tasks/captures-todo.md).
 *
 * Captures land under ``output/captures/<wave>/`` (gitignored). Specs are gated
 * by ``E2E_CAPTURE_TOUR=1`` so they never run in normal CI. Content is the
 * deterministic keyless scripted model + realistic seed data created here so the
 * surfaces look plausible (agent names, tool framing) rather than ``test123``.
 */

export const CAPTURE_ROOT = path.join('..', 'output', 'captures')
export const DESKTOP_VIEWPORT = { width: 1440, height: 960 } as const

export async function capture(page: Page, wave: string, filename: string): Promise<void> {
  const dir = path.join(CAPTURE_ROOT, wave)
  await fs.mkdir(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, filename), fullPage: true })
}

export async function scriptedModelId(request: APIRequestContext): Promise<string> {
  const models = await apiGetJson(request, `${API_BASE}/api/models`)
  if (!Array.isArray(models)) throw new Error('models did not return an array')
  const model = models.find(
    (row) =>
      isRecord(row) && row.provider === 'e2e_scripted' && row.model_name === 'document-artifact-scripted',
  )
  if (!isRecord(model) || typeof model.id !== 'string') {
    throw new Error('E2E scripted model is not seeded')
  }
  return model.id
}

/** Plausible production-shaped agents so dashboards/lists look real, not test-y. */
export const REALISTIC_AGENTS: ReadonlyArray<{
  readonly name: string
  readonly description: string
  readonly system_prompt: string
}> = [
  {
    name: '핏라이프 멤버십 지원봇',
    description: '헬스장 멤버십 문의·예약·취소를 처리하는 고객지원 에이전트',
    system_prompt:
      '당신은 핏라이프 피트니스의 고객지원 상담원입니다. 멤버십 크레딧 조회, 수업 예약, 멤버십 취소를 도와줍니다. 한 번에 하나씩, 친절하고 간결하게 응대하세요.',
  },
  {
    name: '여행 일정 플래너',
    description: '목적지·기간·예산을 받아 맞춤 여행 일정을 설계하는 에이전트',
    system_prompt:
      '당신은 여행 플래너입니다. 사용자의 목적지, 기간, 예산, 취향을 파악해 하루 단위 일정과 추천 장소를 제안합니다.',
  },
  {
    name: '사내 IT 헬프데스크',
    description: '비밀번호 초기화·VPN·장비 요청 등 사내 IT 문의를 처리',
    system_prompt:
      '당신은 사내 IT 헬프데스크 상담원입니다. 비밀번호 초기화, VPN 연결, 장비 신청 절차를 안내합니다.',
  },
  {
    name: '제품 피드백 요약봇',
    description: '고객 리뷰와 설문을 분석해 핵심 인사이트를 요약',
    system_prompt:
      '당신은 제품 피드백 분석가입니다. 고객 리뷰를 주제별로 분류하고 긍정/부정 신호와 개선 우선순위를 요약합니다.',
  },
  {
    name: '마케팅 카피 어시스턴트',
    description: '캠페인 문구·SNS 게시물·이메일 카피 초안을 생성',
    system_prompt:
      '당신은 마케팅 카피라이터입니다. 브랜드 톤에 맞춰 광고 문구, SNS 게시물, 이메일 제목을 여러 안으로 제안합니다.',
  },
]

export async function seedRealisticAgents(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
): Promise<string[]> {
  const modelId = await scriptedModelId(request)
  const ids: string[] = []
  for (const agent of REALISTIC_AGENTS) {
    const created = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
      name: agent.name,
      description: agent.description,
      system_prompt: agent.system_prompt,
      model_id: modelId,
    })
    if (isRecord(created) && typeof created.id === 'string') ids.push(created.id)
  }
  return ids
}

export async function deleteAgents(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  ids: readonly string[],
): Promise<void> {
  for (const id of ids) {
    await request.delete(`${API_BASE}/api/agents/${id}`, { headers: csrfHeaders }).catch(() => {})
  }
}

/** Settle a navigation: network idle + a short paint delay so async lists render. */
export async function settle(page: Page, ms = 800): Promise<void> {
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(ms)
}

// ── Rich seed (wave 7) ──────────────────────────────────────────────────────

export async function installDocxSkill(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
): Promise<string | null> {
  const items = await apiGetJson(
    request,
    `${API_BASE}/api/marketplace/items?resource_type=skill&source_kind=system_seed&limit=200`,
  )
  const list = Array.isArray(items) ? items : []
  const docx = list.find((row) => isRecord(row) && row.slug === 'docx-document')
  if (!isRecord(docx) || typeof docx.id !== 'string') return null
  const installed = await apiPostJson(
    request,
    `${API_BASE}/api/marketplace/items/${docx.id}/install`,
    csrfHeaders,
    { install_mode: 'overwrite_existing' },
  )
  return isRecord(installed) && typeof installed.installed_skill_id === 'string'
    ? installed.installed_skill_id
    : null
}

export async function systemToolIds(request: APIRequestContext, limit: number): Promise<string[]> {
  const tools = await apiGetJson(request, `${API_BASE}/api/tools`)
  const list = Array.isArray(tools) ? tools : []
  return list
    .filter((row): row is Record<string, unknown> => isRecord(row) && typeof row.id === 'string')
    .slice(0, limit)
    .map((row) => row.id as string)
}

/** A fully-configured agent (tools + skill + subagent + trigger) so settings tabs aren't empty. */
export async function createRichAgent(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
): Promise<{ agentId: string; childId: string | null }> {
  const modelId = await scriptedModelId(request)
  const skillId = await installDocxSkill(request, csrfHeaders)
  const toolIds = await systemToolIds(request, 3)
  const child = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name: '예약 처리 보조',
    description: '수업 예약/취소 전용 서브에이전트',
    system_prompt: '예약과 취소만 전담합니다.',
    model_id: modelId,
  })
  const childId = isRecord(child) && typeof child.id === 'string' ? child.id : null
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name: '핏라이프 멤버십 지원봇',
    description: '헬스장 멤버십 문의·예약·취소를 처리하는 고객지원 에이전트',
    system_prompt:
      '당신은 핏라이프 피트니스의 고객지원 상담원입니다. 멤버십 크레딧 조회, 수업 예약, 멤버십 취소를 도와줍니다. 한 번에 하나씩, 친절하고 간결하게 응대하세요.',
    model_id: modelId,
    tool_ids: toolIds,
    skill_ids: skillId ? [skillId] : [],
    sub_agent_ids: childId ? [childId] : [],
  })
  if (!isRecord(agent) || typeof agent.id !== 'string') throw new Error('rich agent create failed')
  return { agentId: agent.id, childId }
}

export async function addIntervalTrigger(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  agentId: string,
  minutes: number,
): Promise<void> {
  await apiPostJson(request, `${API_BASE}/api/agents/${agentId}/triggers`, csrfHeaders, {
    name: '매일 멤버십 리포트',
    trigger_type: 'interval',
    schedule_config: { interval_minutes: minutes },
    input_message: '오늘의 멤버십 현황을 요약해줘.',
  }).catch(() => {})
}

export async function createConversation(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  agentId: string,
  title: string,
): Promise<string> {
  const convo = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${agentId}/conversations`,
    csrfHeaders,
    { title },
  )
  if (!isRecord(convo) || typeof convo.id !== 'string') throw new Error('conversation create failed')
  return convo.id
}

/** A tiny valid 1×1 PNG (red) for an image attachment / lightbox capture. */
export const TINY_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
