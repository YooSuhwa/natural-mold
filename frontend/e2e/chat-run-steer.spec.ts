import { randomUUID } from 'node:crypto'
import type { APIRequestContext, Page } from '@playwright/test'

import { commandStrategy, queueState } from './chat-message-queue-actions'
import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiJson,
  apiPostJson,
  expect,
  isRecord,
  test,
} from './fixtures'
import {
  approveExecuteInSkill,
  commandMethod,
  records,
  sendMessageForRun,
  setupLangGraphV3Agent,
  waitForRunStatus,
} from './langgraph-v3-helpers'

const delegatedToolCallId = 'call_e2e_langgraph_v3_subagent'

async function rootMessages(request: APIRequestContext, conversationId: string) {
  const state = await apiGetJson(
    request,
    `${API_BASE}/api/conversations/${conversationId}/langgraph/threads/${conversationId}/state`,
  )
  if (!isRecord(state) || !isRecord(state.values)) throw new Error('Missing thread state')
  return records(state.values.messages ?? [], 'root checkpoint messages')
}

async function submitSteer(page: Page, runUrl: string, text: string) {
  const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
  await composer.fill(text)
  const [response] = await Promise.all([
    page.waitForResponse((res) => res.url() === runUrl && res.request().method() === 'POST'),
    page.locator('[data-moldy-run-steer-send]').click(),
  ])
  const receipt = await apiJson(response, 'steer submission')
  const submitted: unknown = response.request().postDataJSON()
  if (
    !isRecord(receipt) ||
    !isRecord(submitted) ||
    typeof submitted.client_request_id !== 'string'
  ) {
    throw new Error('Missing submitted steer identity')
  }
  expect(receipt).toMatchObject({
    status: 'pending',
    text,
    client_request_id: submitted.client_request_id,
  })
  await expect(composer).toHaveValue('')
  return { receipt, submitted }
}

test.describe('same-run chat instructions', () => {
  test('receives two ordered instructions during a tool call and applies them once on the original run', async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    const base = `${API_BASE}/api/conversations/${setup.conversationId}`
    const firstText = 'Scope the current task to domestic suppliers.'
    const secondText = 'E2E_DAILY_GREETING please use the daily greeting.'
    const runStarts: string[] = []
    page.on('request', (req) => {
      if (commandMethod(req) === 'run.start') runStarts.push(req.url())
    })
    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const runId = await sendMessageForRun(
        page,
        setup.conversationId,
        `E2E_LANGGRAPH_V3 slow_subagent=true subagent=${setup.childRuntimeName}`,
      )
      const runUrl = `${base}/runs/${runId}/steers`
      // The child tool appears in the live transcript before its delayed result returns.
      await expect(
        page
          .locator('.moldy-tool-pill')
          .getByText('E2E_SUBAGENT_SLOW summarize scoped LangGraph v3 work.', { exact: true })
          .first(),
      ).toBeVisible({ timeout: 30_000 })
      const first = await submitSteer(page, runUrl, firstText)
      const second = await submitSteer(page, runUrl, secondText)
      await expect(
        page.locator('[data-moldy-run-steer-status="pending"]').filter({ hasText: secondText }),
      ).toBeVisible()
      expect(first.receipt.run_id).toBe(runId)
      expect(second.receipt.run_id).toBe(runId)
      const duplicate = await apiPostJson(request, runUrl, setup.csrfHeaders, first.submitted)
      expect(duplicate).toMatchObject({ id: first.receipt.id, sequence: first.receipt.sequence })
      await waitForRunStatus(request, setup.conversationId, runId, 'completed')
      // The scripted provider emits this specific response only when it sees the second instruction.
      await expect(page.getByText(/안녕하세요! 일상을 도와드리는 비서예요/)).toBeVisible()
      const messages = await rootMessages(request, setup.conversationId)
      const toolIndex = messages.findIndex((m) => m.tool_call_id === delegatedToolCallId)
      expect(toolIndex).toBeGreaterThanOrEqual(0)
      expect(JSON.stringify(messages[toolIndex].content)).toContain('scoped tools indexed')
      const applied = messages.filter((m) => m.content === firstText || m.content === secondText)
      expect(applied.map((m) => m.content)).toEqual([firstText, secondText])
      expect(messages.indexOf(applied[0])).toBeGreaterThan(toolIndex)
      expect(
        records(await apiGetJson(request, runUrl), 'run steers').map((s) => [
          s.id,
          s.status,
          s.queued_input_id,
        ]),
      ).toEqual([
        [first.receipt.id, 'applied', null],
        [second.receipt.id, 'applied', null],
      ])
      await page.reload()
      const restored = page.locator('[data-moldy-run-steer-status="applied"]')
      await expect(restored.filter({ hasText: firstText })).toHaveCount(1)
      await expect(restored.filter({ hasText: secondText })).toHaveCount(1)
      expect(runStarts).toHaveLength(1)
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('keeps Enter in the next-request queue and rolls a late steer over once', async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    const base = `${API_BASE}/api/conversations/${setup.conversationId}`
    const lateText = 'Follow up on domestic distributors.'
    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const runId = await sendMessageForRun(page, setup.conversationId, 'E2E_VISUAL_SLOW_STREAM')
      const runUrl = `${base}/runs/${runId}/steers`
      // This fixture streams a final answer, so this run will have no later root model call.
      await expect(page.getByText(/E2E visual stream fixture/).first()).toBeVisible({
        timeout: 30_000,
      })
      const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
      await composer.fill('A separate next request.')
      const [enqueued] = await Promise.all([
        page.waitForResponse((res) => commandStrategy(res.request()) === 'enqueue'),
        composer.press('Enter'),
      ])
      const queued = await apiJson(enqueued, 'keyboard enqueue')
      if (
        !isRecord(queued) ||
        !isRecord(queued.result) ||
        typeof queued.result.input_id !== 'string'
      ) {
        throw new Error('Keyboard enqueue did not return its saved input')
      }
      expect(queued.result.run_id ?? null).toBeNull()
      const nextInputId = queued.result.input_id
      const late = await submitSteer(page, runUrl, lateText)
      await waitForRunStatus(request, setup.conversationId, runId, 'completed')
      await expect
        .poll(async () =>
          records(await apiGetJson(request, runUrl), 'run steers').map((s) => [s.id, s.status]),
        )
        .toEqual([[late.receipt.id, 'queued']])
      await page.reload()
      await expect(
        page.locator('[data-moldy-run-steer-status="queued"]').filter({ hasText: lateText }),
      ).toHaveCount(1)
      const [rolled] = records(await apiGetJson(request, runUrl), 'run steers')
      expect(typeof rolled.queued_input_id).toBe('string')
      expect(rolled.queued_input_id).not.toBe(nextInputId)
      const queue = await queueState(request, setup.conversationId)
      expect(queue.items.filter((row) => row.id === nextInputId)).toHaveLength(1)
      expect(queue.items.filter((row) => row.id === rolled.queued_input_id)).toHaveLength(1)
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
  test('preserves the pending approval against a same-run instruction', async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    const base = `${API_BASE}/api/conversations/${setup.conversationId}`
    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const runId = await sendMessageForRun(page, setup.conversationId, 'E2E_HITL_APPROVAL')
      await waitForRunStatus(request, setup.conversationId, runId, 'interrupted')
      const approval = page.getByTestId('approval-approve-button').last()
      await expect(approval).toBeVisible()
      await expect(page.locator('[data-moldy-run-steer-send]:enabled')).toHaveCount(0)
      const runUrl = `${base}/runs/${runId}/steers`
      const refused = await request.post(runUrl, {
        headers: setup.csrfHeaders,
        data: { client_request_id: randomUUID(), text: 'This must wait for approval.' },
      })
      expect(refused.status()).toBe(409)
      expect(await apiGetJson(request, runUrl)).toEqual([])
      await page.reload()
      await expect(approval).toBeVisible()
      const resumedRunId = await approveExecuteInSkill(page)
      await waitForRunStatus(request, setup.conversationId, resumedRunId, 'completed')
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
})
