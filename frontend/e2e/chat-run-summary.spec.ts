import {
  API_BASE,
  apiDeleteOk,
  apiJson,
  expect,
  isRecord,
  loginApi,
  test,
  type CsrfHeaders,
} from './fixtures'
import { sendMessageForRun, waitForRunStatus } from './langgraph-v3-helpers'
import { observeSummaryResponses } from './helpers/run-summary-observer'
import type { APIRequestContext } from '@playwright/test'

const DIAGNOSTIC_LOCATOR_TIMEOUT_MS = 1_000

async function createScriptedAgent(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
): Promise<string> {
  const models = await apiJson(await request.get(`${API_BASE}/api/models`), 'List models')
  if (!Array.isArray(models) || !models.every(isRecord)) {
    throw new Error('Models response did not return an object array')
  }
  const model = models.find(
    (item) => item.provider === 'e2e_scripted' && item.model_name === 'document-artifact-scripted',
  )
  if (!model || typeof model.id !== 'string') {
    throw new Error('E2E scripted model should be seeded with an id')
  }
  const agent = await apiJson(
    await request.post(`${API_BASE}/api/agents`, {
      headers: csrfHeaders,
      data: {
        name: `E2E Run Summary ${Date.now()}`,
        system_prompt: 'Return the deterministic scripted response.',
        model_id: model.id,
      },
    }),
    'Create scripted agent',
  )
  if (!isRecord(agent) || typeof agent.id !== 'string') {
    throw new Error('Create scripted agent did not return an id')
  }
  return agent.id
}

test.describe('Durable chat run summary', () => {
  test('keeps completed and canceled run summaries associated after reload', async ({
    page,
    request,
    errors,
  }, testInfo) => {
    const csrfHeaders = await loginApi(request)
    const agentId = await createScriptedAgent(request, csrfHeaders)
    let summaryResponses: ReturnType<typeof observeSummaryResponses> | null = null
    let completedRunId: string | null = null

    try {
      const conversation = await apiJson(
        await request.post(`${API_BASE}/api/agents/${agentId}/conversations`, {
          headers: csrfHeaders,
          data: { title: 'Durable run summary' },
        }),
        'Create run-summary conversation',
      )
      if (!isRecord(conversation) || typeof conversation.id !== 'string') {
        throw new Error('Create run-summary conversation did not return an id')
      }
      const conversationId = conversation.id
      summaryResponses = observeSummaryResponses(page, API_BASE, conversationId)

      await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
      completedRunId = await sendMessageForRun(page, conversationId, 'E2E_TOKEN_USAGE_STREAM')

      await expect(
        page.getByText('E2E token usage isolated conversation response.').last(),
      ).toBeVisible({ timeout: 20_000 })
      const summary = page.locator(`[data-testid="run-summary"][data-run-id="${completedRunId}"]`)
      await expect(summary).toHaveCount(1)
      await expect(summary).toBeVisible({ timeout: 15_000 })
      await expect(
        page.locator('[data-moldy-message-role="assistant"]').filter({ has: summary }),
      ).toHaveCount(1)
      await expect(summary).toContainText('도구 총 0')
      await expect(summary).toContainText('서브 에이전트 총 0')
      await summary.getByRole('button', { name: '활동 보기' }).click()
      await expect(summary.getByRole('listitem').first()).toBeVisible()
      await page.screenshot({
        path: testInfo.outputPath('expanded-completed-timeline.png'),
        fullPage: true,
      })
      await testInfo.attach('expanded-completed-timeline', {
        path: testInfo.outputPath('expanded-completed-timeline.png'),
        contentType: 'image/png',
      })

      const persistedRun = await apiJson(
        await request.get(`${API_BASE}/api/conversations/${conversationId}/runs/${completedRunId}`),
        'Get completed run metrics',
      )
      if (!isRecord(persistedRun) || !isRecord(persistedRun.metrics)) {
        throw new Error('Completed run metrics are missing')
      }
      const { elapsed_ms: elapsedMs, activity_json: activityJson } = persistedRun.metrics
      if (typeof elapsedMs !== 'number' || !Array.isArray(activityJson)) {
        throw new Error('Completed run metrics have an invalid elapsed or activity shape')
      }
      expect(Number.isFinite(elapsedMs)).toBeTruthy()
      expect(elapsedMs).toBeGreaterThanOrEqual(0)
      expect(activityJson.length).toBeGreaterThan(0)

      const canceledRunId = await sendMessageForRun(page, conversationId, 'E2E_VISUAL_SLOW_STREAM')
      expect(canceledRunId).not.toBe(completedRunId)
      await page.locator('[data-moldy-stop-button="true"]').click()
      await waitForRunStatus(request, conversationId, canceledRunId, 'canceled')
      const canceledSummary = page.locator(
        `[data-testid="run-summary"][data-run-id="${canceledRunId}"]`,
      )
      await expect(canceledSummary).toBeVisible({ timeout: 15_000 })
      await expect(canceledSummary).toContainText('도구 총')

      await page.reload()
      await expect(
        page.getByText('E2E token usage isolated conversation response.').last(),
      ).toBeVisible({ timeout: 20_000 })
      const restored = page.locator(`[data-testid="run-summary"][data-run-id="${completedRunId}"]`)
      await expect(restored).toBeVisible({ timeout: 15_000 })
      await restored.getByRole('button', { name: '활동 보기' }).click()
      await expect(restored.getByRole('listitem').first()).toBeVisible()
      await expect(
        page.locator(`[data-testid="run-summary"][data-run-id="${canceledRunId}"]`),
      ).toBeVisible({ timeout: 15_000 })
      await page.screenshot({
        path: testInfo.outputPath('restored-completed-canceled-summaries.png'),
        fullPage: true,
      })
      await testInfo.attach('restored-completed-canceled-summaries', {
        path: testInfo.outputPath('restored-completed-canceled-summaries.png'),
        contentType: 'image/png',
      })

      expect(errors).toEqual({ console: [], page: [], network: [] })
    } finally {
      try {
        const targetBubble = page
          .locator('[data-moldy-message-role="assistant"]')
          .filter({ hasText: 'E2E token usage isolated conversation response.' })
        const targetPublicMessageId = await targetBubble
          .getAttribute('data-moldy-message-id')
          .catch(() => null)
        const exactRunSummary = completedRunId
          ? page.locator(`[data-testid="run-summary"][data-run-id="${completedRunId}"]`)
          : null
        const exactRunSummaryCount = exactRunSummary ? await exactRunSummary.count() : 0
        const observation = {
          assistantBubbleCount: await page.locator('[data-moldy-message-role="assistant"]').count(),
          targetAssistantBubbleCount: await targetBubble.count(),
          summaryCount: await page.getByTestId('run-summary').count(),
          targetSummaryCount: await targetBubble.getByTestId('run-summary').count(),
          exactRunSummaryCount,
          targetSummaryAriaExpanded:
            exactRunSummaryCount > 0 && exactRunSummary
              ? await exactRunSummary
                  .locator('button[aria-expanded]')
                  .first()
                  .getAttribute('aria-expanded', { timeout: DIAGNOSTIC_LOCATOR_TIMEOUT_MS })
                  .catch(() => null)
              : null,
          targetSummaryDescendantListItemCount: exactRunSummary
            ? await exactRunSummary.locator('li').count()
            : 0,
          threadRunning: await page
            .locator('[data-moldy-stop-button="true"]')
            .isVisible()
            .catch(() => false),
          ...(summaryResponses
            ? await summaryResponses(targetPublicMessageId, completedRunId)
            : { observationConfigured: false }),
        }
        await testInfo.attach('run-summary-scalar-observation', {
          body: Buffer.from(JSON.stringify(observation, null, 2)),
          contentType: 'application/json',
        })
      } finally {
        await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrfHeaders)
      }
    }
  })
})
