import { API_BASE, apiPostJson, expect, isRecord, test } from './fixtures'
import {
  sendMessage,
  setupLangGraphV3Agent,
  waitForActiveRun,
  waitForRunStatus,
} from './langgraph-v3-helpers'

/**
 * G2 — chat error + retry contract. Drives the scripted-model ``E2E_ERROR`` marker
 * so a run genuinely fails (run.status="failed"), asserts the failed run surfaces an
 * error bubble with a retry button, and that clicking retry drives a fresh run
 * command (fork re-run from the last user checkpoint). Requires the scripted model
 * (E2E_SCRIPTED_MODEL_ENABLED=true).
 */
test.describe('Chat error retry (v3, G2)', () => {
  test('failed run shows an error bubble and retry re-runs from the last user turn', async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)
    const { parentAgentId: agentId, csrfHeaders } = setup

    const convo = await apiPostJson(
      request,
      `${API_BASE}/api/agents/${agentId}/conversations`,
      csrfHeaders,
      { title: 'error-retry' },
    )
    if (!isRecord(convo) || typeof convo.id !== 'string') throw new Error('conversation create failed')
    const conversationId = convo.id

    await page.goto(`/agents/${agentId}/conversations/${conversationId}`, {
      waitUntil: 'domcontentloaded',
      timeout: 180_000,
    })
    await page
      .locator('textarea[data-moldy-composer-input="true"]')
      .last()
      .waitFor({ state: 'visible', timeout: 90_000 })

    // Force a genuine run failure via the scripted-model marker.
    await sendMessage(page, 'E2E_ERROR 강제 실패')
    const runId = await waitForActiveRun(request, conversationId)
    await waitForRunStatus(request, conversationId, runId, 'failed')

    // The failed run renders an error bubble whose retry button is always visible
    // (it lives inside the bubble, not the hover meta row).
    const retryButton = page.getByRole('button', { name: '다시 시도' })
    await expect(retryButton).toBeVisible({ timeout: 30_000 })

    // Clicking retry must send a fresh run command to the backend (fork re-run). If
    // the retry were a no-op (e.g. missing checkpoint), no command request fires.
    const commandRequest = page.waitForRequest(
      (req) => req.url().includes('/commands') && req.method() === 'POST',
      { timeout: 30_000 },
    )
    await retryButton.click()
    await commandRequest

    // The re-run hits E2E_ERROR again and fails, so the error bubble + retry button
    // persists — confirming retry actually drove a new run rather than clearing UI.
    await expect(page.getByRole('button', { name: '다시 시도' })).toBeVisible({ timeout: 30_000 })
  })
})
