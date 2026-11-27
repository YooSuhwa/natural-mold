import { z } from 'zod'

import { API_BASE, apiDeleteOk, apiGetJson, expect, test } from './fixtures'
import { observeMutations, observeStandardStreams, standardCommand } from './helpers/ag-ui-cutover'
import { setupLangGraphV3Agent } from './langgraph-v3-helpers'

const interruptedSnapshot = z.object({
  threadId: z.string(),
  runStatus: z.literal('interrupted'),
  activeRunId: z.null(),
  checkpointId: z.string().min(1),
  interruptRunId: z.string().min(1),
  physicalInterruptRunId: z.string().uuid(),
  interrupts: z.tuple([z.object({ id: z.string().min(1), reason: z.literal('moldy.ask_user') })]),
})

const runHistory = z.object({
  runs: z.array(
    z.object({
      id: z.string(),
      parent_run_id: z.string().nullable(),
      source: z.string(),
      status: z.string(),
    }),
  ),
})

const checkpoint = z.object({
  values: z.object({
    messages: z.array(
      z.object({
        content: z.unknown(),
        tool_call_id: z.string().nullish(),
        tool_calls: z.array(z.object({ id: z.string(), name: z.string() })).optional(),
      }),
    ),
  }),
})

test('submits the first native answer once after the durable approval snapshot arrives', async ({
  page,
  request,
}) => {
  test.setTimeout(120_000)
  const setup = await setupLangGraphV3Agent(request)
  const { conversationId } = setup
  const api = `${API_BASE}/api/conversations/${conversationId}`
  const snapshotUrl = api + '/ag-ui/snapshot'
  const stateUrl = api + '/langgraph/threads/' + conversationId + '/state'
  const held: z.infer<typeof interruptedSnapshot>[] = []
  const release = Promise.withResolvers<void>()
  const writes = observeMutations(page, conversationId)
  const legacyMessageUrl = api + '/messages'
  page.on('request', (req) => {
    if (
      req.method() === 'POST' &&
      (req.url() === legacyMessageUrl || req.url().startsWith(legacyMessageUrl + '/'))
    ) {
      writes.push('legacy message write')
    }
  })
  const finalAnswer = 'E2E ask_user fruit selection received.'
  const selection = JSON.stringify({
    mode: 'option_list',
    selection: ['apple'],
    labels: ['🍎 사과'],
  })

  try {
    await observeStandardStreams(page, conversationId)
    await page.route(snapshotUrl, async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      const original = await route.fetch({ maxRedirects: 0 })
      if (original.ok()) {
        const parsed = interruptedSnapshot.safeParse(await original.json())
        if (parsed.success && parsed.data.threadId === conversationId) {
          held.push(parsed.data)
          // Hold every selected read of this checkpoint so another read cannot enable the form.
          await release.promise
        }
      }
      await route.fulfill({ response: original })
    })
    await page.goto(`/agents/${setup.parentAgentId}/conversations/${conversationId}`)
    const form = page.locator('form[data-moldy-thread-composer]').last()
    const composer = form.locator('textarea[data-moldy-composer-input="true"]')
    await composer.fill('E2E_ASK_USER_FRUIT')
    await expect(form.locator('button[type="submit"]')).toBeEnabled({ timeout: 30_000 })
    const first = await standardCommand(
      page,
      conversationId,
      () => composer.press('Enter'),
      'interrupt',
    )
    expect(first.body.resume ?? []).toEqual([])
    await expect.poll(() => held.length, { timeout: 30_000 }).toBeGreaterThan(0)
    const pending = held[0]
    expect(first.verified.interruptIds).toEqual([pending.interrupts[0].id])
    expect(first.body.runId).toBe(pending.interruptRunId)
    expect(first.verified.toolCallIds).toHaveLength(1)

    // Chromium has received the native terminal event, while the selected GET is still withheld.
    // Any live card present at this point must keep both the choices and the submit button inert.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    )
    const card = page.locator('[data-tool-ui-id]').filter({ hasText: '🍎 사과' })
    await expect(card.locator('button:enabled')).toHaveCount(0)
    const before = runHistory.parse(await apiGetJson(request, api + '/history'))
    expect(before.runs).toEqual([
      expect.objectContaining({ id: pending.physicalInterruptRunId, status: 'interrupted' }),
    ])
    const savedCalls = checkpoint
      .parse(await apiGetJson(request, stateUrl))
      .values.messages.flatMap((message) => message.tool_calls ?? [])
      .filter((call) => call.name === 'ask_user')
    expect(savedCalls).toHaveLength(1)
    expect(writes).toEqual(['ag-ui'])

    release.resolve()
    const apple = card.getByRole('option', { name: /사과/ })
    await expect(apple).toBeEnabled()
    await apple.press('Space')
    await expect(apple).toHaveAttribute('aria-selected', 'true')
    const confirm = card.getByRole('button', { name: /선택 확인|Confirm/ })
    await expect(confirm).toBeEnabled()
    const [resumed, submitted] = await Promise.all([
      standardCommand(page, conversationId, () => confirm.click(), 'success', finalAnswer),
      page.waitForRequest((req) => req.method() === 'POST' && req.url() === api + '/ag-ui/runs'),
    ])
    expect(resumed.body.runId).not.toBe(first.body.runId)
    expect(submitted.postDataJSON()).toMatchObject({
      threadId: conversationId,
      messages: [],
      resume: [
        {
          interruptId: pending.interrupts[0].id,
          status: 'resolved',
          payload: { decisions: [{ type: 'respond', message: selection }] },
        },
      ],
    })
    await expect(page.getByText(finalAnswer, { exact: true })).toBeVisible()
    await expect
      .poll(
        async () => {
          const { runs } = runHistory.parse(await apiGetJson(request, api + '/history'))
          return {
            total: runs.length,
            children: runs
              .filter((run) => run.parent_run_id === pending.physicalInterruptRunId)
              .map((run) => [run.source, run.status]),
          }
        },
        { timeout: 30_000 },
      )
      .toEqual({ total: 2, children: [['resume', 'completed']] })
    const { messages } = checkpoint.parse(await apiGetJson(request, stateUrl)).values
    expect(
      messages
        .filter((message) => message.tool_call_id === savedCalls[0].id)
        .map((message) => message.content),
    ).toEqual([selection])
    expect(messages.filter((message) => message.content === finalAnswer)).toHaveLength(1)
    expect(writes).toEqual(['ag-ui', 'ag-ui'])
  } finally {
    release.resolve()
    await page.unroute(snapshotUrl)
    await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
    await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
  }
})
