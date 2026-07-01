import type { APIRequestContext, Page } from '@playwright/test'
import { API_BASE, apiPostJson, expect, isRecord, test, type CsrfHeaders } from '../fixtures'
import { sendMessage, setupLangGraphV3Agent, waitForActiveRun, waitForRunStatus } from '../langgraph-v3-helpers'
import { capture, captureLocator, DESKTOP_VIEWPORT, settle, warmUpChatRoute } from './_capture-helpers'

/**
 * Wave — chat error + retry (G2). Drives the scripted-model ``E2E_ERROR`` marker
 * so a run genuinely fails (run.status="failed"), then screenshots the resulting
 * error bubble + "다시 시도" retry button, and the state after clicking retry.
 * Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave-chat-error-retry'

async function freshConversation(
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
    timeout: 180_000,
  })
  await page
    .locator('textarea[data-moldy-composer-input="true"]')
    .last()
    .waitFor({ state: 'visible', timeout: 90_000 })
}

test.describe('Chat error + retry captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('captures the error bubble and retry action', async ({ page, request }) => {
    test.setTimeout(300_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const setup = await setupLangGraphV3Agent(request)
    const { parentAgentId: agentId, csrfHeaders } = setup

    const conversationId = await freshConversation(request, csrfHeaders, agentId, '에러 재시도 캡쳐')
    await gotoChat(page, agentId, conversationId)

    // Force a genuine run failure via the scripted-model marker.
    await sendMessage(page, 'E2E_ERROR 강제 실패 시나리오')
    const runId = await waitForActiveRun(request, conversationId)
    await waitForRunStatus(request, conversationId, runId, 'failed')

    // The error bubble carries the always-visible retry button — gate on it so we
    // never screenshot a half-rendered notice.
    const retryButton = page.getByRole('button', { name: '다시 시도' })
    await expect(retryButton).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '01-error-bubble.png')

    // Element-scoped capture of the last assistant (error) bubble — the thread is a
    // nested overflow-y-auto viewport, so a full-page shot can clip a tall message.
    const errorBubble = page.locator('[data-moldy-message-role="assistant"]').last()
    await captureLocator(errorBubble, WAVE, '02-error-bubble-element.png')

    // Retry re-runs from the last user checkpoint (also fails again on the marker),
    // capturing the "retry pressed" transition.
    await retryButton.click()
    await page.waitForTimeout(1_000)
    await capture(page, WAVE, '03-retry-clicked.png')
  })
})
