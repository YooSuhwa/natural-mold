import { API_BASE, apiDeleteOk, expect, test } from './fixtures'
import { expectNoUserTextFlicker, installUserTextStabilityObserver } from './helpers/stability-observers'
import { approveExecuteInSkill, sendMessage, setupLangGraphV3Agent } from './langgraph-v3-helpers'

/**
 * Streaming render INTEGRITY (text-only path via E2E_SLOW_STREAM, ~6 chunks ×
 * 0.75s). Guards the most user-visible chat quality signals during a live
 * stream: the witty loading indicator shows then clears, the user prompt never
 * flickers/disappears, and the optimistic user bubble is never duplicated.
 *
 * Uses the scripted keyless model (E2E_SCRIPTED_MODEL_ENABLED) — CI-runnable,
 * no live LLM. Deliberately text-only so it does NOT depend on the (separately
 * tracked, flaky) subagent-completion path.
 */
test.describe('Chat streaming render integrity', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy', 'Skipped for the legacy chat runtime')

  test('streams text with stable user prompt, no duplicate bubble, and witty loading that clears', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    const wittyLoading = page.locator('[data-moldy-witty-loading="true"]')
    const userBubbles = page.locator('[data-moldy-message-role="user"]')
    const FINAL_TEXT = 'E2E slow stream completed after detached navigation.'

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const prompt = 'E2E_SLOW_STREAM 렌더 무결성 확인'
      await sendMessage(page, prompt)

      // Optimistic user bubble appears exactly once (no duplicate optimistic+real).
      await expect(userBubbles).toHaveCount(1, { timeout: 15_000 })

      // Witty loading is shown while the text streams (no tool/activity/state here,
      // so the witty indicator is the streaming placeholder). Assert presence only
      // — the witty TEXT is intentionally random (Math.random) and must not be asserted.
      await expect(wittyLoading).toBeVisible({ timeout: 15_000 })

      // Observe the user prompt stays visible (no flicker/disappearance) for the
      // whole ~4.5s slow stream until the final text settles.
      await installUserTextStabilityObserver(page, [prompt])
      await expect(page.getByText(FINAL_TEXT)).toBeVisible({ timeout: 30_000 })
      await expectNoUserTextFlicker(page, 1500)

      // Still exactly one user bubble after the run settles.
      await expect(userBubbles).toHaveCount(1)

      // Witty loading clears once the run completes (no lingering placeholder).
      await expect(wittyLoading).toHaveCount(0, { timeout: 15_000 })

      // errors fixture auto-asserts: no page JS exceptions during the stream.
      expect(errors.console, 'console errors during stream').toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('renders exactly one HITL approval card (no duplicate) that resolves on approve', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    const approveButton = page.locator('[data-testid="approval-approve-button"]')
    const userBubbles = page.locator('[data-moldy-message-role="user"]')

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const prompt = 'E2E_HITL_APPROVAL 승인 카드 무결성'
      await sendMessage(page, prompt)

      // The execute_in_skill tool pauses on an approval card (the "called X" box).
      await expect(page.getByText('승인이 필요합니다').last()).toBeVisible({ timeout: 30_000 })
      // The tool name is shown in the card.
      await expect(page.getByText('execute_in_skill').last()).toBeVisible({ timeout: 15_000 })
      // Exactly ONE approval card — no duplicate / stacked cards.
      await expect(approveButton).toHaveCount(1)

      // The user prompt stays stable while the approval card renders.
      await installUserTextStabilityObserver(page, [prompt])
      await expectNoUserTextFlicker(page, 1000)

      // Approve → the card resolves cleanly (approve button removed, no leftover).
      await approveExecuteInSkill(page)
      await expect(approveButton).toHaveCount(0, { timeout: 30_000 })
      await expect(userBubbles).toHaveCount(1)

      expect(errors.console, 'console errors during HITL approval').toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('renders two HITL approval cards (multi-action) and resumes once after approving both', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(150_000)
    const setup = await setupLangGraphV3Agent(request)
    const FINAL_TEXT_PARTIAL = '문서 파일 생성이 완료'
    const cards = page.locator('[data-testid^="approval-action-"]')
    const approveButtons = page.locator('[data-testid="approval-approve-button"]')
    const userBubbles = page.locator('[data-moldy-message-role="user"]')

    // Count resume commands. langchain batches both execute_in_skill calls into ONE
    // interrupt with two action_requests, so the HiTL coordinator must collect both
    // decisions and fire exactly ONE resume carrying both — never one resume per card.
    let resumeCount = 0
    page.on('request', (req) => {
      if (req.method() !== 'POST') return
      if ((req.postData() ?? '').includes('"decisions"')) resumeCount += 1
    })

    const approveCard = async (index: number) => {
      const card = page.getByTestId(`approval-action-${index}`)
      await expect(card).toBeVisible({ timeout: 15_000 })
      const approve = card.getByTestId('approval-approve-button')
      await expect(approve).toBeEnabled({ timeout: 10_000 })
      await approve.click()
    }

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const prompt = 'E2E_HITL_MULTI 멀티 승인 카드 무결성'
      await sendMessage(page, prompt)

      // One AIMessage with two execute_in_skill calls → ONE interrupt → exactly TWO
      // approval cards (no duplicate / stacked / collapsed-into-one).
      await expect(page.getByText('승인이 필요합니다').first()).toBeVisible({ timeout: 30_000 })
      await expect(cards).toHaveCount(2, { timeout: 15_000 })
      await expect(approveButtons).toHaveCount(2)

      // Each card is scoped to its action index and advertises the total action count.
      await expect(page.getByTestId('approval-action-0')).toHaveAttribute(
        'data-hitl-total-actions',
        '2',
      )
      await expect(page.getByTestId('approval-action-1')).toHaveAttribute(
        'data-hitl-total-actions',
        '2',
      )
      // exact: true → match only the tool-name chip, not the description paragraph
      // ("…Tool: execute_in_skill…") which also contains the tool name.
      await expect(
        page.getByTestId('approval-action-0').getByText('execute_in_skill', { exact: true }),
      ).toBeVisible()
      await expect(
        page.getByTestId('approval-action-1').getByText('execute_in_skill', { exact: true }),
      ).toBeVisible()
      await expect(userBubbles).toHaveCount(1, { timeout: 15_000 })

      // User prompt stays stable while both cards render (no flicker/disappearance).
      await installUserTextStabilityObserver(page, [prompt])
      await expectNoUserTextFlicker(page, 1000)

      // Approve only the FIRST card. The coordinator must NOT resume yet: the second
      // card stays pending and the run stays interrupted (no final text).
      await approveCard(0)
      await expect(cards).toHaveCount(1, { timeout: 15_000 })
      await expect(approveButtons).toHaveCount(1)
      await expect(page.getByText(FINAL_TEXT_PARTIAL)).toHaveCount(0)
      expect(resumeCount, 'resume must not fire after only one of two approvals').toBe(0)

      // Approve the SECOND card → coordinator flushes both decisions in ONE resume.
      await approveCard(1)
      await expect(cards).toHaveCount(0, { timeout: 30_000 })
      await expect(approveButtons).toHaveCount(0)
      await expect(page.getByText(FINAL_TEXT_PARTIAL).last()).toBeVisible({ timeout: 60_000 })
      await expect(userBubbles).toHaveCount(1)
      expect(resumeCount, 'exactly one batched resume for both actions').toBe(1)

      expect(errors.console, 'console errors during multi-action HITL').toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
})
