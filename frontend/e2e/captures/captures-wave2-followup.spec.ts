import type { APIRequestContext, Page } from '@playwright/test'
import { API_BASE, apiPostJson, expect, isRecord, test, type CsrfHeaders } from '../fixtures'
import { sendMessage, setupLangGraphV3Agent } from '../langgraph-v3-helpers'
import { capture, DESKTOP_VIEWPORT, settle, warmUpChatRoute } from './_capture-helpers'

/**
 * Wave 2 follow-up 고스트 + 입력 히스토리 캡처:
 *
 *  1막 고스트   : 런 종료 → LLM 후속 제안 1개가 입력창에 연하게(placeholder처럼)
 *  2막 → 수락   : ArrowRight로 제안이 실제 입력으로 채워짐
 *  3막 타이핑   : 한 글자라도 입력하면 고스트가 사라지고 입력만 남음
 *               (지우면 다시 나타나고, Esc로 해제)
 *  4막 히스토리 : ↑로 이전 입력을 최신순 탐색, ↓로 복귀 + draft 복원
 *  5막 토글     : 컴포저 툴바에서 후속 제안 OFF → 런이 끝나도 고스트 없음
 *
 * scripted 모델 배포에선 followup 엔드포인트가 결정적 제안
 * ("방금 답변을 표로 정리해줘")을 돌려줘 전체 체인이 결정적이다.
 * Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave2-followup'
const SUGGESTION = '방금 답변을 표로 정리해줘'
const SCRIPTED_READY = 'E2E scripted document model is ready.'
const FIRST_MESSAGE = '오늘 할 일 정리해줘'
const SECOND_MESSAGE = '어제 회의록 요약해줘'

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

test.describe('Wave 2 follow-up ghost + composer history captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('walks the ghost suggestion + input history story', async ({ page, request }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const setup = await setupLangGraphV3Agent(request)
    const { csrfHeaders } = setup

    const conversationId = await createConversation(
      request,
      csrfHeaders,
      setup.parentAgentId,
      'Follow-up 데모',
    )
    await gotoChat(page, setup.parentAgentId, conversationId)
    const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
    const ghost = page.locator('[data-moldy-followup-ghost]')

    // ── 1막: 런 종료 → 고스트 제안이 입력창에 연하게 나타난다 ───────────
    await sendMessage(page, FIRST_MESSAGE)
    await expect(page.getByText(SCRIPTED_READY).first()).toBeVisible({ timeout: 120_000 })
    await expect(ghost).toBeVisible({ timeout: 30_000 })
    await expect(ghost.getByText(SUGGESTION)).toBeVisible()
    await settle(page)
    await capture(page, WAVE, '00-ghost-suggestion.png')

    // ── 2막: → 키로 제안이 실제 입력으로 채워진다 (고스트는 소진) ────────
    await composer.click()
    await page.keyboard.press('ArrowRight')
    await expect(composer).toHaveValue(SUGGESTION, { timeout: 10_000 })
    await expect(ghost).toBeHidden()
    await settle(page)
    await capture(page, WAVE, '01-ghost-accepted-arrow-right.png')

    // ── 3막: 타이핑하면 사라지고, 지우면 돌아오고, Esc로 해제 ────────────
    await composer.fill('')
    await sendMessage(page, SECOND_MESSAGE)
    await expect(page.getByText(SCRIPTED_READY).nth(1)).toBeVisible({ timeout: 120_000 })
    await expect(ghost).toBeVisible({ timeout: 30_000 })
    await composer.pressSequentially('직')
    await expect(ghost).toBeHidden()
    await settle(page)
    await capture(page, WAVE, '02-ghost-dismissed-by-typing.png')
    // 입력을 지우면 (아직 소진되지 않은) 제안이 다시 나타난다.
    await composer.fill('')
    await expect(ghost).toBeVisible({ timeout: 10_000 })
    // Esc → 이번 제안 해제.
    await composer.press('Escape')
    await expect(ghost).toBeHidden()

    // ── 4막: ↑/↓ 입력 히스토리 — 최신순 탐색 + draft 복원 ────────────────
    await composer.pressSequentially('작성 중이던 초안')
    await composer.press('ArrowUp')
    await expect(composer).toHaveValue(SECOND_MESSAGE, { timeout: 10_000 })
    await composer.press('ArrowUp')
    await expect(composer).toHaveValue(FIRST_MESSAGE, { timeout: 10_000 })
    await settle(page)
    await capture(page, WAVE, '03-history-arrow-up.png')
    await composer.press('ArrowDown')
    await expect(composer).toHaveValue(SECOND_MESSAGE, { timeout: 10_000 })
    await composer.press('ArrowDown')
    // 최신을 지나 내려오면 작성 중이던 draft가 복원된다 (readline 계약).
    await expect(composer).toHaveValue('작성 중이던 초안', { timeout: 10_000 })
    await settle(page)
    await capture(page, WAVE, '04-history-draft-restored.png')

    // ── 5막: 토글 OFF — 런이 끝나도 고스트가 뜨지 않는다 ────────────────
    await composer.fill('')
    const toggle = page.locator('[data-moldy-followup-toggle]').last()
    await expect(toggle).toHaveAttribute('data-moldy-followup-toggle', 'on')
    await toggle.click()
    await expect(toggle).toHaveAttribute('data-moldy-followup-toggle', 'off')
    await sendMessage(page, '토글 끈 상태 확인')
    await expect(page.getByText(SCRIPTED_READY).nth(2)).toBeVisible({ timeout: 120_000 })
    await expect(ghost).toBeHidden()
    await settle(page)
    await capture(page, WAVE, '05-followup-toggle-off.png')

    // 정리 — 다음 spec을 위해 토글 복원.
    await toggle.click()
    await expect(toggle).toHaveAttribute('data-moldy-followup-toggle', 'on')
  })
})
