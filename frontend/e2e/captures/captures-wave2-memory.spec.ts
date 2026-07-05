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
import { sendMessage, setupLangGraphV3Agent } from '../langgraph-v3-helpers'
import { capture, DESKTOP_VIEWPORT, settle, warmUpChatRoute } from './_capture-helpers'

/**
 * Wave 2 메모리 라이프사이클 캡처 — 채팅의 메모리 관련 표면 전부:
 *
 *  1막 자동 저장 : write_policy=auto + E2E_MEMORY_SAVE → "저장됨" pill (즉시 기록)
 *  2막 저장 제안 : write_policy=ask + E2E_MEMORY_PROPOSE → 제안 카드
 *                 (저장/수정/거절 버튼, 기본 펼침)
 *  3막 저장 승인 : 제안 카드에서 저장 클릭 → "저장됨" 상태 전이
 *  4막 저장 거절 : 새 제안 → 거절 클릭 → "저장 안 함" 상태 전이
 *  5막 수정 후 저장: 새 제안 → 수정 → textarea 편집 → 수정 후 저장
 *  6막 회상 풀서클: 저장된 기억들이 다음 런의 "기억 참고" 칩으로 돌아온다
 *
 * Gated by E2E_CAPTURE_TOUR=1 (+ E2E_TEST_HELPERS_ENABLED, scripted model).
 */

const WAVE = 'wave2-memory'
const MEMORY_FINAL = 'E2E memory tool run complete.'
const SAVE_CONTENT = '사용자는 결론 먼저, 표 중심의 보고서를 선호한다.'
const PROPOSE_CONTENT = '매주 월요일 아침에 주간 계획 브리핑을 받고 싶어한다.'

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

async function setWritePolicy(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  policy: 'auto' | 'ask',
): Promise<void> {
  const response = await request.patch(`${API_BASE}/api/me/memory-settings`, {
    headers: csrfHeaders,
    data: { memory_write_policy: policy },
  })
  expect(response.ok()).toBe(true)
}

/** 재실행/리트라이가 결정적이도록 기존 기억을 전부 지운다. */
async function clearMemories(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
): Promise<void> {
  const existing = await apiGetJson(request, `${API_BASE}/api/memories?scope=all`)
  if (!Array.isArray(existing)) return
  for (const row of existing) {
    if (!isRecord(row) || typeof row.id !== 'string') continue
    const response = await request.delete(`${API_BASE}/api/memories/${row.id}`, {
      headers: csrfHeaders,
    })
    expect(response.ok()).toBe(true)
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

test.describe('Wave 2 memory lifecycle captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('walks the memory save/propose/approve/reject/edit/recall lifecycle', async ({
    page,
    request,
  }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const setup = await setupLangGraphV3Agent(request)
    const { csrfHeaders } = setup
    await clearMemories(request, csrfHeaders)

    try {
      // ── 1막: 자동 저장 (write_policy=auto) — 묻지 않고 바로 기록 ────────
      await setWritePolicy(request, csrfHeaders, 'auto')
      const autoConversationId = await createConversation(
        request,
        csrfHeaders,
        setup.parentAgentId,
        '메모리 자동 저장',
      )
      await gotoChat(page, setup.parentAgentId, autoConversationId)
      await sendMessage(page, '내 보고서 취향 꼭 기억해줘 E2E_MEMORY_SAVE')
      await expect(page.getByText(MEMORY_FINAL).first()).toBeVisible({ timeout: 120_000 })

      const memoryCard = page.getByTestId('memory-tool-card').last()
      await expect(memoryCard.getByText('저장됨')).toBeVisible({ timeout: 30_000 })
      // 접힌 pill에도 내용 미리보기(meta)가 보인다 — 펼쳐서 전문 확인.
      await memoryCard.getByText('저장됨').click()
      // 내용은 접힘 meta(truncate span)와 펼침 본문(p) 두 곳에 나온다 — first로 고정.
      await expect(memoryCard.getByText(SAVE_CONTENT).first()).toBeVisible({ timeout: 10_000 })
      await settle(page)
      await capture(page, WAVE, '00-memory-auto-saved-pill.png')

      // ── 2막: 저장 제안 (write_policy=ask) — 승인 전엔 기록하지 않는다 ────
      await setWritePolicy(request, csrfHeaders, 'ask')
      const proposeConversationId = await createConversation(
        request,
        csrfHeaders,
        setup.parentAgentId,
        '메모리 저장 제안',
      )
      await gotoChat(page, setup.parentAgentId, proposeConversationId)
      await sendMessage(page, '월요일 브리핑 얘기 기억해두면 좋겠어 E2E_MEMORY_PROPOSE')
      await expect(page.getByText(MEMORY_FINAL).first()).toBeVisible({ timeout: 120_000 })

      const proposalCard = page.getByTestId('memory-tool-card').last()
      await expect(proposalCard.getByText('저장 제안')).toBeVisible({ timeout: 30_000 })
      // 제안 카드는 기본 펼침 — 내용 + 저장/수정/거절 버튼이 바로 보인다.
      await expect(proposalCard.getByText(PROPOSE_CONTENT).first()).toBeVisible({ timeout: 10_000 })
      await expect(proposalCard.getByTestId('memory-proposal-approve')).toBeVisible()
      await expect(proposalCard.getByTestId('memory-proposal-reject')).toBeVisible()
      await expect(proposalCard.getByTestId('memory-proposal-edit')).toBeVisible()
      await settle(page)
      await capture(page, WAVE, '01-memory-proposal-card.png')

      // ── 3막: 저장 승인 — 카드가 "저장됨"으로 전이 ───────────────────────
      await proposalCard.getByTestId('memory-proposal-approve').click()
      await expect(proposalCard.getByText('저장됨')).toBeVisible({ timeout: 30_000 })
      await settle(page)
      await capture(page, WAVE, '02-memory-proposal-approved.png')

      // ── 4막: 저장 거절 — 카드가 "저장 안 함"으로 전이 ───────────────────
      const rejectConversationId = await createConversation(
        request,
        csrfHeaders,
        setup.parentAgentId,
        '메모리 저장 거절',
      )
      await gotoChat(page, setup.parentAgentId, rejectConversationId)
      await sendMessage(page, '이건 저장하지 말자 E2E_MEMORY_PROPOSE')
      await expect(page.getByText(MEMORY_FINAL).first()).toBeVisible({ timeout: 120_000 })
      const rejectCard = page.getByTestId('memory-tool-card').last()
      await expect(rejectCard.getByTestId('memory-proposal-reject')).toBeVisible({
        timeout: 30_000,
      })
      await rejectCard.getByTestId('memory-proposal-reject').click()
      await expect(rejectCard.getByText('저장 안 함')).toBeVisible({ timeout: 30_000 })
      await settle(page)
      await capture(page, WAVE, '03-memory-proposal-rejected.png')

      // ── 5막: 수정 후 저장 — 제안 내용을 고쳐서 승인 ─────────────────────
      const editConversationId = await createConversation(
        request,
        csrfHeaders,
        setup.parentAgentId,
        '메모리 수정 후 저장',
      )
      await gotoChat(page, setup.parentAgentId, editConversationId)
      await sendMessage(page, '브리핑 시간을 기억해줘 E2E_MEMORY_PROPOSE')
      await expect(page.getByText(MEMORY_FINAL).first()).toBeVisible({ timeout: 120_000 })
      const editCard = page.getByTestId('memory-tool-card').last()
      await expect(editCard.getByTestId('memory-proposal-edit')).toBeVisible({ timeout: 30_000 })
      await editCard.getByTestId('memory-proposal-edit').click()
      const editor = editCard.getByRole('textbox')
      await expect(editor).toBeVisible({ timeout: 10_000 })
      await editor.fill('매주 월요일 오전 9시에 주간 계획 브리핑을 받고 싶어한다.')
      await settle(page)
      await capture(page, WAVE, '04-memory-proposal-editing.png')
      await editCard.getByTestId('memory-proposal-edit-approve').click()
      await expect(editCard.getByText('저장됨')).toBeVisible({ timeout: 30_000 })
      await expect(
        editCard.getByText('매주 월요일 오전 9시에 주간 계획 브리핑을 받고 싶어한다.').first(),
      ).toBeVisible()
      await settle(page)
      await capture(page, WAVE, '05-memory-proposal-edit-approved.png')

      // ── 6막: 회상 풀서클 — 저장된 기억이 다음 런의 회상 칩으로 돌아온다 ──
      // 저장분: 자동 저장 1 + 승인 1 + 수정 후 저장 1 = 3개.
      const recallConversationId = await createConversation(
        request,
        csrfHeaders,
        setup.parentAgentId,
        '기억 회상 확인',
      )
      await gotoChat(page, setup.parentAgentId, recallConversationId)
      await sendMessage(page, '지난번에 말한 취향대로 정리해줘')
      const recallChip = page.locator('[data-moldy-memory-recall]')
      await expect(recallChip).toBeVisible({ timeout: 60_000 })
      await expect(recallChip.getByText('3개')).toBeVisible({ timeout: 15_000 })
      await recallChip.getByText('기억 참고').click()
      await expect(recallChip.getByText(SAVE_CONTENT).first()).toBeVisible({ timeout: 10_000 })
      await settle(page)
      await capture(page, WAVE, '06-memory-recall-full-circle.png')

      // 리로드 — 영속 이벤트는 기억 내용이 <redacted>로 마스킹되고(공유 안전
      // 계약), 소유자 화면은 메모리 API 조인으로 내용을 복원해야 한다.
      await gotoChat(page, setup.parentAgentId, recallConversationId)
      await expect(recallChip).toBeVisible({ timeout: 60_000 })
      await recallChip.getByText('기억 참고').click()
      await expect(recallChip.getByText(SAVE_CONTENT).first()).toBeVisible({ timeout: 15_000 })
      await expect(recallChip.getByText('<redacted>')).toHaveCount(0)
    } finally {
      // 정리 — 정책 기본값(ask) 복원 + 기억/제안 잔여물 제거로 spec 간 결합 차단.
      await setWritePolicy(request, csrfHeaders, 'ask')
      await clearMemories(request, csrfHeaders)
    }
  })
})
