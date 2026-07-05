import type { APIRequestContext, Page } from '@playwright/test'
import {
  API_BASE,
  apiGetJson,
  apiPostJson,
  expect,
  isRecord,
  test,
  type CsrfHeaders,
} from '../fixtures'
import {
  approveExecuteInSkill,
  sendMessage,
  setupLangGraphV3Agent,
} from '../langgraph-v3-helpers'
import { capture, DESKTOP_VIEWPORT, settle, warmUpChatRoute } from './_capture-helpers'

/**
 * Wave 2 시나리오 캡처 — "기억하고, 찾고, 팀으로 일하는 에이전트" 스토리:
 *
 *  1막 기억   : 장기 기억 2개 심기 → 런 시작 시 기억 회상 칩(moldy.memory_recalled)
 *  2막 검색   : E2E_SEARCH_RICH — answer 요약 박스 + 리치 결과 카드,
 *               E2E_SEARCH_SHOP — Naver items shape 썸네일 + 최저가 카드
 *  3막 팀     : E2E_LANGGRAPH_V3 미션 런 — 서브에이전트 팀 스트립(라이브/완료),
 *               execute_in_skill 승인 → 터미널 ui_data 카드(genui 첫 실도구 producer)
 *  4막 리로드 : 스킬 실행 pill(커맨드+파일 칩) + 팀 스트립 복원
 *
 * Gated by E2E_CAPTURE_TOUR=1 (+ E2E_TEST_HELPERS_ENABLED, scripted model).
 */

const WAVE = 'wave2-scenario'
const FINAL_TEXT = 'E2E LangGraph v3 validation complete'
const RICH_FINAL = 'E2E rich search rendering complete.'
const SHOP_FINAL = 'E2E shop search rendering complete.'

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

/** 기존 기억을 전부 지우고 정확히 원하는 세트만 남긴다 — retry/재실행에서
 * 기억이 누적되어 "기억 참고 2개" 단언이 깨지는 것을 막는 rerun-safe 헬퍼. */
async function resetMemories(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  records: readonly Record<string, unknown>[],
): Promise<void> {
  const existing = await apiGetJson(request, `${API_BASE}/api/memories?scope=all`)
  if (Array.isArray(existing)) {
    for (const row of existing) {
      if (!isRecord(row) || typeof row.id !== 'string') continue
      const response = await request.delete(`${API_BASE}/api/memories/${row.id}`, {
        headers: csrfHeaders,
      })
      expect(response.ok()).toBe(true)
    }
  }
  for (const data of records) {
    const record = await apiPostJson(request, `${API_BASE}/api/memories`, csrfHeaders, data)
    if (!isRecord(record) || typeof record.id !== 'string') throw new Error('memory create failed')
  }
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

test.describe('Wave 2 scenario captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('walks the Wave 2 story and captures every surface', async ({ page, request }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const setup = await setupLangGraphV3Agent(request)
    const { csrfHeaders } = setup

    // ── 1막: 장기 기억 심기 — 다음 런부터 회상 칩이 뜬다 ────────────────
    await resetMemories(request, csrfHeaders, [
      {
        scope: 'user',
        content: '답변은 한국어로, 결론부터 정리하는 것을 선호한다.',
      },
      {
        scope: 'agent',
        agent_id: setup.parentAgentId,
        content: '리포트는 표와 3줄 요약 중심으로 작성한다.',
      },
    ])

    // ── 2막: 검색 리치카드 — answer 요약 박스 + 회상 칩 동시 확인 ────────
    const searchConversationId = await createConversation(
      request,
      csrfHeaders,
      setup.parentAgentId,
      'Wave2 검색 리서치',
    )
    await gotoChat(page, setup.parentAgentId, searchConversationId)
    await sendMessage(page, 'agentic os 조사해줘 E2E_SEARCH_RICH')
    await expect(page.getByText(RICH_FINAL).first()).toBeVisible({ timeout: 120_000 })

    // 기억 회상 칩 — stream head 이벤트로 도착, 런 종료 후에도 상시 표시.
    const memoryChip = page.locator('[data-moldy-memory-recall]')
    await expect(memoryChip).toBeVisible({ timeout: 30_000 })
    await expect(memoryChip.getByText('2개')).toBeVisible({ timeout: 10_000 })

    // 검색 pill — Tavily answer 요약 박스 + 결과 카드 3장 (단독 호출이라 펼침).
    const answerBox = page.locator('[data-moldy-search-answer]')
    await expect(answerBox).toBeVisible({ timeout: 30_000 })
    await expect(answerBox.getByText(/에이전틱 OS는/)).toBeVisible()
    await expect(page.getByText('Agentic OS 아키텍처 개요')).toBeVisible()
    await settle(page)
    await capture(page, WAVE, '00-search-rich-answer-and-memory-chip.png')

    // 회상 칩 펼침 — scope 배지 + 기억 미리보기.
    await memoryChip.getByText('기억 참고').click()
    await expect(memoryChip.getByText('답변은 한국어로, 결론부터 정리하는 것을 선호한다.')).toBeVisible(
      { timeout: 10_000 },
    )
    await settle(page)
    await capture(page, WAVE, '01-memory-recall-expanded.png')
    await memoryChip.getByText('기억 참고').click()

    // 쇼핑 검색 — Naver items shape: 썸네일 + 최저가 + 판매처.
    await sendMessage(page, '무선 키보드 최저가 알려줘 E2E_SEARCH_SHOP')
    await expect(page.getByText(SHOP_FINAL).first()).toBeVisible({ timeout: 120_000 })
    const priceRow = page.locator('[data-moldy-search-price]').first()
    await expect(priceRow).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('[data-moldy-search-thumbnail]').first()).toBeVisible()
    await expect(page.getByText('최저 42,900원')).toBeVisible()
    await settle(page)
    await capture(page, WAVE, '02-search-shop-thumbnail-price.png')

    // ── 3막: 팀 미션 런 — 팀 스트립 + 스킬 승인 + 터미널 카드 ───────────
    const missionConversationId = await createConversation(
      request,
      csrfHeaders,
      setup.parentAgentId,
      'Wave2 팀 미션',
    )
    await gotoChat(page, setup.parentAgentId, missionConversationId)
    await sendMessage(
      page,
      `위키 리포트 준비해줘 E2E_LANGGRAPH_V3 subagent=${setup.childRuntimeName}`,
    )

    // 팀 스트립 — 위임 즉시 서브에이전트 칩이 뜨고 표시명으로 치환된다.
    const teamStrip = page.locator('[data-moldy-team-strip]')
    await expect(teamStrip).toBeVisible({ timeout: 90_000 })
    await expect(teamStrip.getByText(setup.childName)).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '03-team-strip-live.png')

    // execute_in_skill 승인 → docx 스킬이 실제 실행된다.
    await approveExecuteInSkill(page)
    await expect(page.getByText(FINAL_TEXT).first()).toBeVisible({ timeout: 180_000 })

    // 팀 스트립 완료 상태 — done/total 메타.
    await expect(teamStrip.getByText('1/1 완료')).toBeVisible({ timeout: 30_000 })

    // 터미널 ui_data 카드 — 첫 실도구 genui producer (execute_in_skill stdout).
    const terminalCard = page.getByTestId('data-ui-terminal').last()
    await expect(terminalCard).toBeVisible({ timeout: 60_000 })
    await terminalCard.scrollIntoViewIfNeeded()
    await settle(page)
    await capture(page, WAVE, '04-team-strip-done-terminal-card.png')

    // 팀 스트립 칩 클릭 → 우측 레일 서브에이전트 상세.
    await teamStrip.getByText(setup.childName).click()
    const rail = page.getByRole('complementary')
    await expect(rail.getByText(setup.childName).first()).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '05-team-chip-opens-rail.png')

    // ── 4막: 리로드 — 스킬 실행 pill(커맨드+파일 칩) + 팀 스트립 복원 ─────
    await gotoChat(page, setup.parentAgentId, missionConversationId)
    await expect(teamStrip).toBeVisible({ timeout: 90_000 })
    const skillPill = page.locator('[data-moldy-skill-execution="docx-document"]').last()
    await expect(skillPill).toBeVisible({ timeout: 60_000 })
    // 파일이 있으면 기본 펼침 — OUTPUT_FILES 칩이 파일 API로 링크된다.
    const fileChip = skillPill.locator('[data-moldy-skill-file]').first()
    await expect(fileChip).toBeVisible({ timeout: 30_000 })
    await skillPill.scrollIntoViewIfNeeded()
    await settle(page)
    await capture(page, WAVE, '06-skill-pill-files-after-reload.png')

    // ── 정리: user-scope 기억은 계정 전역이라 다른 spec 화면에 "기억 참고"
    // 칩이 새어 들어간다 — 투어가 만든 기억을 지워 spec 간 결합을 끊는다.
    await resetMemories(request, csrfHeaders, [])
  })
})
