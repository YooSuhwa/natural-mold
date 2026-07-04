import type { APIRequestContext, Page } from '@playwright/test'
import { API_BASE, apiGetJson, apiPostJson, expect, isRecord, test, type CsrfHeaders } from '../fixtures'
import {
  approveExecuteInSkill,
  sendMessage,
  setupLangGraphV3Agent,
} from '../langgraph-v3-helpers'
import { capture, captureLocator, DESKTOP_VIEWPORT, settle, warmUpChatRoute } from './_capture-helpers'

/**
 * Wave 1 시나리오 캡처 — "에이전트 팀의 하루" 스토리로 Wave 1 기능을 순서대로 시연:
 *
 *  1막 온보딩  : OpenWiki 템플릿 원클릭 에이전트 → 빈 화면 능력 칩 + usage_example 스타터
 *  2막 미션    : E2E_LANGGRAPH_V3 런 — 미션 컨트롤 라이브 → HITL 승인 →
 *               런 종료 후에도 남는 체크리스트, 서브에이전트 pill 결과 요약,
 *               아티팩트 코드 하이라이트(wave1_demo.py)
 *  3막 커서    : E2E_SLOW_STREAM 미드스트림 타이핑 캐럿
 *  4막 다이제스트: 스케줄 활동 배지(밤샘 다이제스트) — E2E activity 헬퍼로 시뮬레이션
 *
 * Gated by E2E_CAPTURE_TOUR=1 (+ E2E_TEST_HELPERS_ENABLED, scripted model).
 */

const WAVE = 'wave1-scenario'
const OPENWIKI_TEMPLATE_NAME = 'OpenWiki 문서화 에이전트'
const FINAL_TEXT = 'E2E LangGraph v3 validation complete'
const CODE_ARTIFACT = 'wave1_demo.py'

async function findOpenWikiTemplateId(request: APIRequestContext): Promise<string> {
  const templates = await apiGetJson(request, `${API_BASE}/api/templates`)
  if (!Array.isArray(templates)) throw new Error('templates list failed')
  const tpl = templates.find(
    (item) => isRecord(item) && item.name === OPENWIKI_TEMPLATE_NAME,
  )
  if (!isRecord(tpl) || typeof tpl.id !== 'string') throw new Error('OpenWiki template missing')
  return tpl.id
}

async function findScriptedModelId(request: APIRequestContext): Promise<string> {
  const models = await apiGetJson(request, `${API_BASE}/api/models`)
  if (!Array.isArray(models)) throw new Error('models list failed')
  const model = models.find((item) => isRecord(item) && item.provider === 'e2e_scripted')
  if (!isRecord(model) || typeof model.id !== 'string') throw new Error('scripted model missing')
  return model.id
}

async function createConversation(
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

async function gotoChat(page: Page, agentId: string, conversationId: string): Promise<void> {
  await page.goto(`/agents/${agentId}/conversations/${conversationId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 280_000,
  })
  await page
    .locator('textarea[data-moldy-composer-input="true"]')
    .last()
    .waitFor({ state: 'visible', timeout: 120_000 })
}

test.describe('Wave 1 scenario captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('walks the Wave 1 story and captures every surface', async ({ page, request }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const setup = await setupLangGraphV3Agent(request)
    const { csrfHeaders } = setup

    // ── 1막: 온보딩 — 템플릿 원클릭 에이전트의 빈 화면 (능력 칩 + 스타터) ──
    const templateId = await findOpenWikiTemplateId(request)
    const modelId = await findScriptedModelId(request)
    const onboardAgent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
      name: OPENWIKI_TEMPLATE_NAME,
      system_prompt: 'wave1 onboarding demo',
      model_id: modelId,
      template_id: templateId,
      identity_mode: 'per_user',
    })
    if (!isRecord(onboardAgent) || typeof onboardAgent.id !== 'string') {
      throw new Error('template agent create failed')
    }
    await gotoChat(page, onboardAgent.id, 'new')
    const capabilities = page.locator('[data-moldy-empty-capabilities]')
    await expect(capabilities).toBeVisible({ timeout: 60_000 })
    await expect(capabilities.getByText('openwiki')).toBeVisible()
    const starter = page.locator('[data-moldy-empty-starters] button').first()
    await expect(starter).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '00-empty-state-capabilities-starter.png')

    await starter.click()
    await expect(page.locator('textarea[data-moldy-composer-input="true"]').last()).toHaveValue(
      /저장소의 위키를 만들어줘/,
      { timeout: 10_000 },
    )
    await settle(page)
    await capture(page, WAVE, '01-starter-filled-composer.png')

    // ── 2막: 미션 — 계획·위임·아티팩트가 있는 풀 런 ─────────────────────
    const missionConversationId = await createConversation(
      request,
      csrfHeaders,
      setup.parentAgentId,
      'Wave1 미션 런',
    )
    await gotoChat(page, setup.parentAgentId, missionConversationId)
    await sendMessage(
      page,
      `바이럴 리포트 준비해줘 E2E_LANGGRAPH_V3 code_artifact=true subagent=${setup.childRuntimeName}`,
    )

    // 미션 컨트롤 바 — 스트리밍 중 라이브 체크리스트.
    const missionControl = page.locator('[data-moldy-mission-control]')
    await expect(missionControl).toBeVisible({ timeout: 60_000 })
    await expect(missionControl.getByText('1/3 완료')).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '02-mission-control-live.png')

    // HITL 승인 2회 — code_artifact 흐름은 write_file → execute_in_skill 순으로
    // 순차 인터럽트되고, 프론트는 연속 승인 카드를 그룹으로 묶어 보여준다.
    await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
      timeout: 90_000,
    })
    await approveExecuteInSkill(page)
    // 두 번째 승인(스킬 실행)이 그룹 카드에 합류한 순간을 캡처. 그룹(compact)
    // 모드에서는 개별 "승인이 필요합니다" 헤드라인이 없으므로 헬퍼 대신 남은
    // 승인 버튼을 직접 클릭한다.
    await expect(page.getByText(/승인 대기 2건/)).toBeVisible({ timeout: 90_000 })
    const pendingApprove = page.getByTestId('approval-approve-button')
    await expect
      .poll(async () => pendingApprove.count(), { timeout: 30_000, intervals: [500, 1_000] })
      .toBeGreaterThan(0)
    await settle(page)
    await capture(page, WAVE, '03-grouped-approvals.png')
    await pendingApprove.last().click()
    await expect(page.getByText(FINAL_TEXT).first()).toBeVisible({ timeout: 120_000 })

    // 런이 끝나도 미션 컨트롤은 남는다 — 펼쳐서 체크리스트 캡처.
    await expect(missionControl).toBeVisible()
    await missionControl.getByText('작업 목록').click()
    await expect(missionControl.getByText('Collect LangGraph v3 runtime evidence')).toBeVisible({
      timeout: 10_000,
    })
    await settle(page)
    await capture(page, WAVE, '04-mission-control-after-run.png')
    await missionControl.getByText('작업 목록').click()

    // 완료된 서브에이전트 pill — 접힌 상태에서도 결과 첫 줄 요약이 보인다.
    const subagentSummary = page.locator('[data-moldy-subagent-summary]').first()
    await expect(subagentSummary).toBeVisible({ timeout: 30_000 })
    await expect(subagentSummary).toContainText('E2E subagent')
    await settle(page)
    await capture(page, WAVE, '05-subagent-pill-summary.png')

    // 아티팩트 레일 — write_file로 만든 파이썬 파일이 하이라이트되어 보인다.
    await page.getByRole('button', { name: /파일 패널|Artifacts/ }).click()
    const rail = page.getByRole('complementary')
    const codeArtifactButton = rail.getByRole('button', { name: new RegExp(CODE_ARTIFACT) }).last()
    await expect(codeArtifactButton).toBeVisible({ timeout: 30_000 })
    await codeArtifactButton.click()
    // 하이라이터가 토큰 단위 span으로 코드를 렌더한다 — dataclass 토큰 가시성으로 검증.
    await expect(rail.getByText('dataclass').first()).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '06-artifact-code-highlight.png')

    // ── 3막: 스트리밍 타이핑 캐럿 (미드스트림) ───────────────────────────
    const caretConversationId = await createConversation(
      request,
      csrfHeaders,
      setup.parentAgentId,
      'Wave1 캐럿 데모',
    )
    await gotoChat(page, setup.parentAgentId, caretConversationId)
    await sendMessage(page, '느린 답변으로 스트리밍을 보여줘 E2E_SLOW_STREAM')
    const stopButton = page.locator('[data-moldy-stop-button="true"]:visible').last()
    await stopButton.waitFor({ state: 'visible', timeout: 30_000 })
    // slow stream은 청크당 0.75초 — 두 청크쯤 흐른 시점에 캐럿을 캡처.
    await page.waitForTimeout(1_800)
    await capture(page, WAVE, '07-streaming-caret.png')
    await stopButton.waitFor({ state: 'hidden', timeout: 90_000 })

    // ── 4막: 밤샘 다이제스트 — 스케줄 활동 배지 ─────────────────────────
    const activityResponse = await request.patch(
      `${API_BASE}/api/e2e/conversations/${missionConversationId}/activity`,
      {
        headers: csrfHeaders,
        data: { last_activity_source: 'schedule', unread_count: 3 },
      },
    )
    expect(activityResponse.ok()).toBe(true)
    await page.reload({ waitUntil: 'domcontentloaded' })
    const scheduleBadge = page.locator(
      `[data-moldy-schedule-activity="${missionConversationId}"]`,
    )
    await expect(scheduleBadge).toBeVisible({ timeout: 60_000 })
    await settle(page)
    await capture(page, WAVE, '08-schedule-digest-badge.png')
    const sessionRow = page.locator(
      `[data-chat-session-href*="${missionConversationId}"]`,
    )
    await captureLocator(sessionRow, WAVE, '09-schedule-digest-row.png')
  })
})
