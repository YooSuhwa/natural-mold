import { createHash, randomUUID } from 'node:crypto'
import type { APIRequestContext, Page } from '@playwright/test'
import { z } from 'zod'

import { API_BASE, apiDeleteOk, apiGetJson, apiJson, expect, test } from '../fixtures'
import { queueState, sendForStrategy } from '../chat-message-queue-actions'
import {
  company,
  databaseState,
  observeMutations,
  openReplica,
  requiredEnvironment,
  savedMessages,
  snapshotResponse,
  standardCommand,
} from '../helpers/ag-ui-cutover'
import {
  FINAL_TEXT,
  NOTES_FILE,
  REPORT_FILE,
  expectFinalTextVisible,
  sendMessage,
  sendMessageForRun,
  setupLangGraphV3Agent,
  waitForArtifact,
  waitForRunStatus,
} from '../langgraph-v3-helpers'

const readyAnswer = 'E2E scripted document model is ready.'
const finalAnswer =
  'E2E LangGraph v3 validation complete: todos, subagent, artifact, usage, and replay are ready.'
const approval = (page: Page) =>
  page.getByTestId('approval-approve-button').filter({ visible: true })
const polling = { timeout: 60_000, intervals: [300, 500, 1000] }

async function versions(request: APIRequestContext, conversationId: string) {
  const result = z
    .array(z.object({ id: z.string(), display_name: z.string() }))
    .parse(await apiGetJson(request, `${API_BASE}/api/conversations/${conversationId}/artifacts`))
  return Promise.all(
    [REPORT_FILE, NOTES_FILE].map(async (displayName) => {
      const items = result.filter((item) => item.display_name === displayName)
      expect(items).toHaveLength(1)
      const response = await request.get(`${API_BASE}/api/artifacts/${items[0].id}/download`)
      expect(response.status()).toBe(200)
      const bytes = await response.body()
      expect(bytes.length).toBeGreaterThan(0)
      return {
        id: items[0].id,
        displayName,
        sha256: createHash('sha256').update(bytes).digest('hex'),
      }
    }),
  )
}

// Dedicated two-replica cutover stack. The primary Next build uses AG-UI, the rollback build
// uses langgraph_v3, and their browser origins share the session cookie's loopback hostname.
test('reads one delegated chat across replicas, approval, AG-UI cutover and rollback', async ({
  browser,
  context,
  page,
  request,
}, testInfo) => {
  test.setTimeout(300_000)
  const primaryUrl = testInfo.project.use.baseURL
  if (!primaryUrl) throw new Error('The AG-UI gate needs a frontend baseURL')
  const rollbackUrl = requiredEnvironment('E2E_AGUI_LEGACY_FRONTEND_URL')
  expect(new URL(rollbackUrl).origin).not.toBe(new URL(primaryUrl).origin)
  const setup = await setupLangGraphV3Agent(request)
  const { conversationId, parentAgentId } = setup
  const path = `/agents/${parentAgentId}/conversations/${conversationId}`
  const standardUrl = new URL(path, primaryUrl).href
  const previousUrl = new URL(path, rollbackUrl).href
  const base = `${API_BASE}/api/conversations/${conversationId}`
  const replica = await openReplica(browser, context)
  const outsider = await openReplica(browser, null)
  const reader = await replica.newPage()
  const observer = await outsider.newPage()
  const writes = observeMutations(page, conversationId)
  const readerWrites = observeMutations(reader, conversationId)
  const oldQuestion = `기존 대화 ${randomUUID()}`
  try {
    // Persist a conversation through the actual old UI before enabling the standard reader.
    await page.goto(previousUrl)
    const historicalRun = await sendMessageForRun(page, conversationId, oldQuestion)
    await waitForRunStatus(request, conversationId, historicalRun, 'completed')
    await expect(page.getByText(readyAnswer, { exact: true }).first()).toBeVisible()
    const [original] = (await savedMessages(request, conversationId)).filter(
      (m) => m.role === 'user' && m.content === oldQuestion,
    )
    expect(original?.branch_checkpoint_id).toEqual(expect.any(String))
    expect(writes).toEqual(['run.start'])
    const beforeCutover = databaseState(conversationId)

    await Promise.all([snapshotResponse(page, conversationId, 'a'), page.goto(standardUrl)])
    await expect(page.getByText(oldQuestion, { exact: true })).toBeVisible()
    expect(databaseState(conversationId)).toEqual(beforeCutover)
    const question = `새 채팅 통신 ${randomUUID()}`
    const ordinary = await standardCommand(
      page,
      conversationId,
      () => sendMessage(page, question),
      'success',
      readyAnswer,
    )
    await expect(page.getByText(question, { exact: true })).toBeVisible()
    expect(ordinary.body.resume ?? []).toHaveLength(0)
    const baseline = databaseState(conversationId)
    expect(baseline.counts.runs).toBe(beforeCutover.counts.runs + 1)

    // Restore through the other replica while the slow delegated child is still running.
    const delegated = standardCommand(
      page,
      conversationId,
      () =>
        sendMessage(page, `E2E_LANGGRAPH_V3 subagent=${setup.childRuntimeName} slow_subagent=true`),
      'interrupt',
    )
    // Await both operations so a failed send can never leave an unhandled rejected promise.
    const [interrupted, attached] = await Promise.all([
      delegated,
      (async () => {
        await expect
          .poll(
            () =>
              databaseState(conversationId).runs.filter((run) => run.status === 'running').length,
            polling,
          )
          .toBe(1)
        const [attach] = await Promise.all([
          reader.waitForResponse((response) => {
            const path = new URL(response.url()).pathname
            return (
              response.request().method() === 'GET' &&
              path.startsWith(`/api/conversations/${conversationId}/ag-ui/`) &&
              path.endsWith('/stream')
            )
          }),
          snapshotResponse(reader, conversationId, 'b'),
          reader.goto(standardUrl),
        ])
        expect(attach.status()).toBe(200)
        expect(await attach.headerValue('x-moldy-e2e-worker')).toBe('b')
        await expect(reader.getByText(/E2E subagent visual matrix:/).first()).toBeVisible({
          timeout: 45_000,
        })
        await expect(approval(reader)).toBeVisible({ timeout: 75_000 })
        return new URL(attach.url())
      })(),
    ])
    const attachRunId = attached.searchParams.get('runId') ?? attached.pathname.split('/').at(-2)
    expect(decodeURIComponent(attachRunId ?? '')).toBe(interrupted.body.runId)
    expect(interrupted.verified.interruptIds).toHaveLength(1)
    await expect(approval(page)).toBeVisible()
    await expect
      .poll(
        () =>
          databaseState(conversationId).runs.filter((run) => run.status === 'interrupted').length,
        polling,
      )
      .toBe(1)
    const pending = databaseState(conversationId)
    expect(pending.counts.runs).toBe(baseline.counts.runs + 1)
    expect(pending.skillExecutions).toBe(baseline.skillExecutions)
    expect(readerWrites).toEqual([])

    // Roll back the initiating tab during a pending approval. This is a transport read on each UI.
    await page.goto(previousUrl)
    await expect(approval(page)).toBeVisible()
    await expect(page.getByText('docx-document').last()).toBeVisible()
    expect(databaseState(conversationId)).toEqual(pending)
    expect(writes).toEqual(['run.start', 'ag-ui', 'ag-ui'])

    // A real second-company login must not learn whether this chat or its child output exists.
    const login = await outsider.request.post(`${API_BASE}/api/auth/login`, {
      data: {
        email: requiredEnvironment('MOLDY_AGUI_OTHER_EMAIL'),
        password: requiredEnvironment('MOLDY_AGUI_OTHER_PASSWORD'),
      },
    })
    expect(login.ok()).toBeTruthy()
    const [mine, foreign] = await Promise.all([company(request), company(outsider.request)])
    expect(foreign.id).not.toBe(mine.id)
    expect(foreign.tenant_id).not.toBe(mine.tenant_id)
    const denied = await outsider.request.get(`${base}/ag-ui/snapshot`)
    const missing = await outsider.request.get(
      `${API_BASE}/api/conversations/${randomUUID()}/ag-ui/snapshot`,
    )
    expect([denied.status(), missing.status()]).toEqual([404, 404])
    expect(await denied.text()).not.toContain(oldQuestion)
    const hiddenStream = await outsider.request.get(
      `${base}/ag-ui/runs/${encodeURIComponent(interrupted.body.runId)}/stream`,
    )
    expect(hiddenStream.status()).toBe(404)
    await Promise.all([
      observer.waitForResponse(
        (response) =>
          response.request().method() === 'GET' &&
          new URL(response.url()).pathname === `/api/conversations/${conversationId}/messages` &&
          response.status() === 404,
      ),
      observer.goto(standardUrl),
    ])
    await expect(
      observer
        .getByRole('alert')
        .filter({ hasText: /대화 유형을 확인할 수 없습니다|chat type could not be verified/i }),
    ).toBeVisible()
    await expect(observer.getByText(oldQuestion, { exact: true })).toHaveCount(0)
    expect(databaseState(conversationId)).toEqual(pending)

    // The original owner resolves the approval on replica B.
    const resumed = await standardCommand(
      reader,
      conversationId,
      () => approval(reader).click(),
      'success',
      finalAnswer,
    )
    expect(resumed.body.runId).not.toEqual(interrupted.body.runId)
    expect(resumed.body.resume?.map((entry) => [entry.interruptId, entry.status])).toEqual([
      [interrupted.verified.interruptIds[0], 'resolved'],
    ])
    await expectFinalTextVisible(reader)
    await expect(reader.getByText(setup.childRuntimeName).first()).toBeVisible()
    await Promise.all([
      waitForArtifact(request, conversationId, REPORT_FILE),
      waitForArtifact(request, conversationId, NOTES_FILE),
    ])
    await expect
      .poll(() => databaseState(conversationId).skillExecutions, polling)
      .toBe(pending.skillExecutions + 1)
    const completed = databaseState(conversationId)
    expect(completed.counts.runs).toBe(pending.counts.runs + 1)
    const interruptedPhysical = pending.runs.find((run) => run.status === 'interrupted')
    expect(
      completed.runs.filter(
        (run) => run.status === 'completed' && run.parent_run_id === interruptedPhysical?.id,
      ),
    ).toHaveLength(1)
    const files = await versions(request, conversationId)

    const finalBubble = reader
      .locator('[data-moldy-message-role="assistant"]')
      .filter({ hasText: FINAL_TEXT })
      .last()
    const [feedback] = await Promise.all([
      reader.waitForResponse(
        (res) =>
          res.request().method() === 'POST' && new URL(res.url()).pathname.endsWith('/feedback'),
      ),
      finalBubble.getByRole('button', { name: /도움이 됨|Helpful|Thumbs up/ }).click(),
    ])
    expect(feedback.status()).toBeLessThan(300)
    const messages = await savedMessages(request, conversationId)
    const finals = messages.filter((m) => m.role === 'assistant' && m.content === finalAnswer)
    expect(finals).toHaveLength(1)
    expect(finals[0].feedback).toMatchObject({ rating: 'up' })
    expect(messages.find((m) => m.id === original.id)?.branch_checkpoint_id).toBe(
      original.branch_checkpoint_id,
    )

    await page.reload()
    await expectFinalTextVisible(page)
    await expect(page.getByText(setup.childRuntimeName).first()).toBeVisible()
    await expect(page.getByText(oldQuestion, { exact: true })).toBeVisible()
    await Promise.all([snapshotResponse(reader, conversationId, 'b'), reader.reload()])
    await expectFinalTextVisible(reader)
    expect(await versions(request, conversationId)).toEqual(files)
    expect(await savedMessages(request, conversationId)).toEqual(messages)
    expect(databaseState(conversationId)).toEqual(completed)
    expect(readerWrites).toEqual(['ag-ui'])
    expect(writes).toEqual(['run.start', 'ag-ui', 'ag-ui'])
    await testInfo.attach('cutover', {
      contentType: 'application/json',
      body: JSON.stringify({
        conversationId,
        historicalRun,
        publicRuns: [ordinary.body.runId, interrupted.body.runId, resumed.body.runId],
        persistedRuns: completed.runs,
        oldMessageId: original.id,
        pinnedCheckpoint: original.branch_checkpoint_id,
        feedbackId: finals[0].id,
        artifacts: files,
        writes,
        readerWrites,
      }),
    })
  } finally {
    await Promise.all([outsider.close(), replica.close()])
    await apiDeleteOk(request, `${API_BASE}/api/agents/${parentAgentId}`, setup.csrfHeaders)
    await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
  }
})

test('preserves a paused goal, pending steer and queued input through an in-flight transport change', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(160_000)
  const primaryUrl = testInfo.project.use.baseURL
  if (!primaryUrl) throw new Error('The AG-UI gate needs a frontend baseURL')
  const setup = await setupLangGraphV3Agent(request)
  const { conversationId, parentAgentId, csrfHeaders } = setup
  const path = `/agents/${parentAgentId}/conversations/${conversationId}`
  const previousUrl = new URL(path, requiredEnvironment('E2E_AGUI_LEGACY_FRONTEND_URL')).href
  const base = `${API_BASE}/api/conversations/${conversationId}`
  const goalSchema = z.object({
    goal: z.object({
      id: z.string(),
      revision: z.number(),
      version: z.number(),
      status: z.string(),
      title: z.string(),
    }),
  })
  const steerSchema = z.object({
    id: z.string(),
    sequence: z.number(),
    client_request_id: z.string(),
    status: z.string(),
    run_id: z.string(),
    queued_input_id: z.string().nullable(),
  })
  const mutations = observeMutations(page, conversationId)
  try {
    const goalTitle = `목표 전환 ${randomUUID()}`
    const created = goalSchema.parse(
      await apiJson(
        await request.put(`${base}/goal`, {
          headers: csrfHeaders,
          data: {
            expected_revision: 0,
            title: goalTitle,
            criteria: [{ kind: 'manual', description: '검토 후 계속' }],
          },
        }),
        'create goal',
      ),
    )
    const paused = goalSchema.parse(
      await apiJson(
        await request.post(`${base}/goal/actions`, {
          headers: csrfHeaders,
          data: {
            expected_revision: created.goal.revision,
            action: 'pause',
          },
        }),
        'pause goal',
      ),
    )
    expect(paused.goal.status).toBe('paused')
    await page.goto(previousUrl)
    await expect(page.locator('#conversation-goal')).toContainText(goalTitle)
    const runId = await sendMessageForRun(page, conversationId, 'E2E_VISUAL_SLOW_STREAM')
    await expect(page.getByText(/E2E visual stream fixture/).first()).toBeVisible()
    const queueText = `대기 입력 ${randomUUID()}`
    const queued = await sendForStrategy(page, queueText, 'enqueue')
    expect(queued.runId).toBeNull()
    const runUrl = `${base}/runs/${runId}/steers`
    const steerText = `작업 중 보충 ${randomUUID()}`
    await page.locator('textarea[data-moldy-composer-input="true"]').last().fill(steerText)
    const [accepted] = await Promise.all([
      page.waitForResponse((res) => res.request().method() === 'POST' && res.url() === runUrl),
      page.locator('[data-moldy-run-steer-send]').click(),
    ])
    const steer = steerSchema.parse(await apiJson(accepted, 'steer receipt'))
    expect(steer).toMatchObject({ run_id: runId, status: 'pending', queued_input_id: null })
    await Promise.all([
      snapshotResponse(page, conversationId, 'a'),
      page.goto(new URL(path, primaryUrl).href),
    ])
    await expect(page.locator('#conversation-goal')).toContainText(goalTitle)
    await waitForRunStatus(request, conversationId, runId, 'completed')
    const readSteers = async () => z.array(steerSchema).parse(await apiGetJson(request, runUrl))
    await expect
      .poll(async () => (await readSteers()).map((item) => [item.id, item.status]), polling)
      .toEqual([[steer.id, 'queued']])
    const [rolled] = await readSteers()
    expect(rolled).toMatchObject({
      sequence: steer.sequence,
      client_request_id: steer.client_request_id,
    })
    expect(rolled.queued_input_id).not.toBeNull()
    expect(rolled.queued_input_id).not.toBe(queued.inputId)
    const visibleReceipt = page
      .locator('[data-moldy-run-steer-status="queued"]')
      .filter({ hasText: steerText })
    await expect(visibleReceipt).toBeVisible({ timeout: 30_000 })
    await page.goto(previousUrl)
    await expect(page.locator('#conversation-goal')).toContainText(goalTitle)
    await expect(visibleReceipt).toBeVisible({ timeout: 30_000 })
    expect(await readSteers()).toEqual([rolled])
    const currentGoal = goalSchema.parse(await apiGetJson(request, `${base}/goal`))
    expect(currentGoal.goal).toEqual(paused.goal)
    const queue = await queueState(request, conversationId)
    for (const inputId of [queued.inputId, rolled.queued_input_id]) {
      expect(queue.items.filter((item) => item.id === inputId)).toHaveLength(1)
    }
    const observed = databaseState(conversationId)
    expect(observed.runs.length).toBeLessThanOrEqual(3)
    expect(observed.inputs.map((item) => item.id).sort()).toEqual(
      queue.items.map((item) => item.id).sort(),
    )
    expect(mutations).toEqual(['run.start', 'run.start'])
    await testInfo.attach('steer-and-goal-cutover', {
      contentType: 'application/json',
      body: JSON.stringify({
        conversationId,
        runId,
        goalId: paused.goal.id,
        goalRevision: paused.goal.revision,
        steerId: steer.id,
        movedInputId: rolled.queued_input_id,
        nextInputId: queued.inputId,
        physicalRuns: observed.runs,
      }),
    })
  } finally {
    await apiDeleteOk(request, `${API_BASE}/api/agents/${parentAgentId}`, csrfHeaders)
    await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, csrfHeaders)
  }
})
