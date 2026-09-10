import type { Request } from '@playwright/test'

import { API_BASE, apiDeleteOk, expect, test } from './fixtures'
import {
  beginPersistedEnqueue,
  commandStrategy,
  expectUniqueTranscriptTurn,
  inputHasExactHuman,
  queueState,
  runStartHasExactHuman,
  sendForStrategy,
  type QueueItem,
} from './chat-message-queue-actions'
import { sendMessageForRun, setupLangGraphV3Agent, waitForRunStatus } from './langgraph-v3-helpers'

test.describe('Server-backed chat message queue', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  test('enqueues, edits, reorders, deletes, and restores pending messages after reload', async ({
    page,
    request,
  }, testInfo) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    try {
      const active = await request.post(
        `${API_BASE}/api/e2e/conversations/${setup.conversationId}/runs`,
        {
          headers: setup.csrfHeaders,
          data: { status: 'running', source: 'chat', input_preview: 'queue fixture' },
        },
      )
      expect(active.ok()).toBeTruthy()
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)

      const { inputId: firstId } = await sendForStrategy(page, 'first queued message', 'enqueue')
      const { inputId: secondId } = await sendForStrategy(page, 'second queued message', 'enqueue')
      await expect(page.locator(`[data-moldy-queue-item="${firstId}"]`)).toBeVisible()
      await expect(page.locator(`[data-moldy-queue-item="${secondId}"]`)).toBeVisible()

      await page
        .locator(`[data-moldy-queue-item="${firstId}"]`)
        .getByRole('button', { name: /더보기|More/ })
        .click()
      await page.locator(`[data-moldy-queue-edit="${firstId}"]`).click()
      const editor = page.getByRole('textbox', { name: /대기 메시지 수정|Edit queued message/ })
      await editor.fill('edited queued message')
      await page.getByRole('button', { name: /저장|Save/ }).click()
      await page
        .locator(`[data-moldy-queue-item="${firstId}"]`)
        .getByRole('button', { name: /더보기|More/ })
        .click()
      await page.locator(`[data-moldy-queue-move-down="${firstId}"]`).click()

      await expect
        .poll(async () =>
          (await queueState(request, setup.conversationId)).items.map((item) => item.id),
        )
        .toEqual([secondId, firstId])

      await page
        .locator(`[data-moldy-queue-item="${secondId}"]`)
        .getByRole('button', { name: /더보기|More/ })
        .click()
      await page.locator(`[data-moldy-queue-remove="${secondId}"]`).click()
      await expect
        .poll(async () => {
          const state = await queueState(request, setup.conversationId)
          return state.items.find((item) => item.id === secondId)?.status
        })
        .toBe('canceled')

      await page.reload()
      await expect(page.locator(`[data-moldy-queue-item="${firstId}"]`)).toContainText(
        'edited queued message',
      )
      await expect(page.locator(`[data-moldy-queue-item="${secondId}"]`)).toHaveCount(0)
      await page.screenshot({ path: testInfo.outputPath('queue-crud-reload.png'), fullPage: true })
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('stop pauses pending work, reload preserves it, and resume dispatches it', async ({
    page,
    request,
  }, testInfo) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const activeRunId = await sendMessageForRun(
        page,
        setup.conversationId,
        'E2E_VISUAL_SLOW_STREAM queue stop',
      )
      const stopButton = page.locator('[data-moldy-stop-button="true"]')
      await expect(stopButton).toBeVisible({ timeout: 10_000 })
      await expect(stopButton).toBeEnabled({ timeout: 10_000 })
      const pending = await beginPersistedEnqueue(
        page,
        request,
        setup.conversationId,
        'durable after stop',
      )
      await stopButton.click()
      const queuedResponse = await pending.responsePromise
      expect(queuedResponse.ok()).toBeTruthy()
      const queuedId = pending.inputId
      await waitForRunStatus(request, setup.conversationId, activeRunId, 'canceled')
      await expect
        .poll(async () => (await queueState(request, setup.conversationId)).paused)
        .toBe(true)

      await page.reload()
      await expect(page.locator(`[data-moldy-queue-item="${queuedId}"]`)).toBeVisible()
      await page.locator('[data-moldy-queue-resume]').click()

      let resumedRunId = ''
      await expect
        .poll(async () => {
          const item = (await queueState(request, setup.conversationId)).items.find(
            (candidate) => candidate.id === queuedId,
          )
          resumedRunId = item?.run_id ?? ''
          return item?.status === 'claimed' && resumedRunId ? resumedRunId : null
        })
        .not.toBeNull()
      await waitForRunStatus(request, setup.conversationId, resumedRunId, 'completed')
      await expectUniqueTranscriptTurn(
        page,
        'durable after stop',
        'E2E scripted document model is ready.',
      )
      await page.screenshot({
        path: testInfo.outputPath('queue-stop-resume-live.png'),
        fullPage: true,
      })
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('explicit steer interrupts and follows the accepted input without a duplicate submit', async ({
    page,
    request,
  }, testInfo) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    const expectedHuman = 'explicit steer message'
    const expectedAssistant = 'E2E scripted document model is ready.'
    const runStarts: Request[] = []
    page.on('request', (value) => {
      try {
        const body = value.postDataJSON() as { method?: unknown }
        if (body.method === 'run.start') runStarts.push(value)
      } catch {
        // Non-JSON requests are unrelated to run submission.
      }
    })
    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const predecessorId = await sendMessageForRun(
        page,
        setup.conversationId,
        'E2E_VISUAL_SLOW_STREAM interrupt queue',
      )
      const steerButton = page.locator('[data-moldy-queue-steer-new]')
      await expect(steerButton).toBeVisible({ timeout: 10_000 })
      const accepted = await sendForStrategy(page, expectedHuman, 'interrupt', {
        afterSteerArmed: async () => {
          expect(runStarts).toHaveLength(1)
          await waitForRunStatus(request, setup.conversationId, predecessorId, 'running', 5_000)
        },
      })
      expect(accepted.runId).toBeNull()
      await waitForRunStatus(request, setup.conversationId, predecessorId, 'canceled')

      let acceptedRunId = ''
      let acceptedInput: QueueItem | undefined
      await expect
        .poll(async () => {
          const input = (await queueState(request, setup.conversationId)).items.find(
            (item) => item.id === accepted.inputId,
          )
          acceptedInput = input
          acceptedRunId = input?.run_id ?? ''
          return input?.status
        })
        .toBe('claimed')
      expect(acceptedRunId).not.toBe('')
      await expect.poll(() => runStarts.length, { timeout: 5_000, intervals: [250, 500] }).toBe(2)
      const secondRunStart = runStarts.at(1)
      if (!secondRunStart) throw new Error('explicit steer did not issue a second run.start')
      expect(commandStrategy(secondRunStart)).toBe('interrupt')
      expect(runStartHasExactHuman(secondRunStart, expectedHuman)).toBe(true)
      expect(inputHasExactHuman(acceptedInput?.input_payload, expectedHuman)).toBe(true)
      await waitForRunStatus(request, setup.conversationId, acceptedRunId, 'completed')
      await expect(page.locator(`[data-moldy-queue-item="${accepted.inputId}"]`)).toHaveCount(0, {
        timeout: 10_000,
      })
      await expectUniqueTranscriptTurn(page, expectedHuman, expectedAssistant)
      await page.screenshot({ path: testInfo.outputPath('queue-steer-live.png'), fullPage: true })
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
})
