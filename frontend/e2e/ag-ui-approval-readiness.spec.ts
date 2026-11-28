import type { APIRequestContext } from '@playwright/test'
import { z } from 'zod'

import { textContentFromMessageContent } from '../src/lib/chat/langgraph-runtime/stream-message-content'
import { API_BASE, apiDeleteOk, apiGetJson, apiPostJson, expect, loginApi, test } from './fixtures'
import { observeMutations, observeStandardStreams, standardCommand } from './helpers/ag-ui-cutover'
import { observeSelectedApprovalReads } from './helpers/approval-readiness-fetch'
import { SCRIPTED_MODEL, SCRIPTED_PROVIDER } from './langgraph-v3-helpers'

const question = z.object({
  mode: z.literal('option_list'),
  question: z.string().min(1),
  minSelections: z.literal(1),
  maxSelections: z.literal(1),
  options: z.tuple([
    z.object({ id: z.literal('apple'), label: z.literal('🍎 사과') }),
    z.object({ id: z.literal('grape'), label: z.literal('🍇 포도') }),
    z.object({ id: z.literal('pear'), label: z.literal('🍐 배') }),
  ]),
})
const answerSchema = z.object({
  properties: z.object({
    decisions: z.object({
      minItems: z.literal(1),
      maxItems: z.literal(1),
      items: z.literal(false),
      prefixItems: z.tuple([
        z.object({
          properties: z.object({
            type: z.object({ const: z.literal('respond') }),
            message: z.object({ type: z.literal('string') }),
          }),
        }),
      ]),
    }),
  }),
})
const slot = z.object({
  id: z.string().min(1),
  reason: z.string(),
  expiresAt: z.string().nullish(),
  responseSchema: z.unknown(),
  metadata: z.object({ moldy: z.unknown() }).nullish(),
})
const nativeQuestion = z.object({ type: z.literal('ask_user') }).and(question)
const reviewedQuestion = z.object({
  action_requests: z.tuple([z.object({ name: z.literal('ask_user'), args: question })]),
  review_configs: z.tuple([
    z.object({
      action_name: z.literal('ask_user'),
      allowed_decisions: z.tuple([z.literal('respond')]),
    }),
  ]),
})
type Profile = 'native' | 'middleware-backed'
function matchingQuestion(value: z.infer<typeof slot>, profile: Profile) {
  const payload = value.metadata?.moldy
  if (
    !answerSchema.safeParse(value.responseSchema).success ||
    (value.expiresAt != null && !(Date.parse(value.expiresAt) > Date.now())) ||
    z.object({ actionable: z.literal(false) }).safeParse(payload).success
  )
    return false
  return profile === 'native'
    ? value.reason === 'moldy.ask_user' && nativeQuestion.safeParse(payload).success
    : value.reason === 'moldy.tool_approval' && reviewedQuestion.safeParse(payload).success
}

function reasonLabel(reason?: string) {
  if (!reason) return 'none'
  return (
    ['ask_user', 'tool_approval', 'auth_required', 'input_required'].find(
      (name) => reason === 'moldy.' + name,
    ) ?? 'other'
  )
}

const interruptedSnapshot = z.object({
  threadId: z.string(),
  runStatus: z.literal('interrupted'),
  activeRunId: z.null(),
  checkpointId: z.string().min(1),
  runId: z.string().min(1),
  latestPhysicalRunId: z.string().uuid(),
  interruptRunId: z.string().min(1),
  physicalInterruptRunId: z.string().uuid(),
  interrupts: z.tuple([slot]),
})
const snapshotStatus = z.object({ runStatus: z.string().nullish() })
const snapshotSlots = z.object({ interrupts: z.array(z.object({ reason: z.string() })) })

async function setupQuestion(request: APIRequestContext, profile: Profile) {
  const csrfHeaders = await loginApi(request)
  const models = z
    .array(z.object({ id: z.string().uuid(), provider: z.string(), model_name: z.string() }))
    .parse(await apiGetJson(request, API_BASE + '/api/models'))
  const model = models.find(
    (row) => row.provider === SCRIPTED_PROVIDER && row.model_name === SCRIPTED_MODEL,
  )
  if (!model) throw new Error('The scripted question model is missing.')
  // Set agent policy before the conversation takes its immutable runtime snapshot.
  const { id: agentId } = z.object({ id: z.string().uuid() }).parse(
    await apiPostJson(request, API_BASE + '/api/agents', csrfHeaders, {
      name: 'E2E question ' + profile + ' ' + Date.now(),
      system_prompt: 'Ask the scripted question and acknowledge its answer.',
      model_id: model.id,
      middleware_configs:
        profile === 'native'
          ? [{ type: 'human_in_the_loop', params: { interrupt_on: { ask_user: false } } }]
          : [],
    }),
  )
  try {
    const { id: conversationId } = z
      .object({ id: z.string().uuid() })
      .parse(
        await apiPostJson(
          request,
          API_BASE + '/api/agents/' + agentId + '/conversations',
          csrfHeaders,
          { title: 'E2E question readiness' },
        ),
      )
    return { agentId, conversationId, csrfHeaders }
  } catch (error) {
    await apiDeleteOk(request, API_BASE + '/api/agents/' + agentId, csrfHeaders)
    throw error
  }
}

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

const textBlock = z.object({ type: z.literal('text'), text: z.string() })
const checkpoint = z.object({
  values: z.object({
    messages: z.array(
      z.object({
        id: z.string().min(1),
        type: z.string(),
        content: z.union([
          z.string(),
          z.array(z.unknown()).transform((parts) =>
            parts.flatMap((part) => {
              if (typeof part === 'string') return [{ type: 'text' as const, text: part }]
              const block = textBlock.safeParse(part)
              return block.success ? [block.data] : []
            }),
          ),
        ]),
        tool_call_id: z.string().nullish(),
        tool_calls: z.array(z.object({ id: z.string(), name: z.string() })).optional(),
      }),
    ),
  }),
})

for (const profile of ['native', 'middleware-backed'] as const) {
  test(
    'submits the first ' + profile + ' answer once after the durable approval snapshot arrives',
    async ({ page, request }, testInfo) => {
      test.setTimeout(120_000)
      const setup = await setupQuestion(request, profile)
      const { conversationId } = setup
      const api = `${API_BASE}/api/conversations/${conversationId}`
      const snapshotUrl = api + '/ag-ui/snapshot'
      const stateUrl = api + '/langgraph/threads/' + conversationId + '/state'
      const held: z.infer<typeof interruptedSnapshot>[] = []
      const release = Promise.withResolvers<void>()
      const observations = {
        selectedRequests: 0,
        targetedRequests: 0,
        interceptedReads: 0,
        successfulReads: 0,
        interruptedReads: 0,
        malformedReads: 0,
        heldReads: 0,
        resumePosts: 0,
        resumePostsBeforeRelease: 0,
        completedResumeRuns: 0,
        savedAnswers: 0,
        lastHttpStatus: 0,
        terminalReason: 'none',
        lastSnapshotReason: 'none',
        sameThread: false,
        sameRun: false,
        sameSlot: false,
        samePhysicalRun: false,
        hasCheckpoint: false,
        actionable: false,
        sameActionContract: false,
        lockedBeforeRelease: false,
        effectVerified: false,
      }
      const selectedUrl = new URL(snapshotUrl)
      const sameSnapshotEndpoint = (url: URL) =>
        url.origin === selectedUrl.origin && url.pathname === selectedUrl.pathname
      let terminalQuestionMatches = false
      const writes = observeMutations(page, conversationId)
      const legacyMessageUrl = api + '/messages'
      page.on('request', (req) => {
        const url = new URL(req.url())
        if (req.method() === 'GET' && sameSnapshotEndpoint(url)) {
          if (url.searchParams.has('runId')) observations.targetedRequests++
          else if (!url.search) observations.selectedRequests++
        }
        if (req.method() === 'POST' && req.url() === api + '/ag-ui/runs') {
          const post = z
            .object({ resume: z.array(z.unknown()).min(1) })
            .safeParse(req.postDataJSON())
          if (post.success) observations.resumePosts++
        }
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

      let released = false
      let stopWatching: (() => Promise<void>) | undefined
      async function inspectSelection(httpStatus: number, body: unknown) {
        if (released) return
        observations.interceptedReads++
        observations.lastHttpStatus = httpStatus
        if (httpStatus >= 200 && httpStatus < 300) {
          observations.successfulReads++
          const status = snapshotStatus.safeParse(body)
          if (!status.success) observations.malformedReads++
          if (status.success && status.data.runStatus === 'interrupted') {
            observations.interruptedReads++
            const declared = snapshotSlots.safeParse(body)
            observations.lastSnapshotReason = reasonLabel(
              declared.success ? declared.data.interrupts[0]?.reason : undefined,
            )
            const parsed = interruptedSnapshot.safeParse(body)
            if (!parsed.success) observations.malformedReads++
            else {
              const saved = parsed.data
              observations.sameThread = saved.threadId === conversationId
              observations.hasCheckpoint = !!saved.checkpointId
              observations.samePhysicalRun =
                saved.latestPhysicalRunId === saved.physicalInterruptRunId
              observations.actionable = matchingQuestion(saved.interrupts[0], profile)
              if (
                observations.sameThread &&
                observations.samePhysicalRun &&
                saved.runId === saved.interruptRunId &&
                observations.actionable
              ) {
                held.push(saved)
                observations.heldReads++
                // Other selected reads of this checkpoint must wait for the same release.
                await release.promise
              }
            }
          }
        }
      }
      async function releaseSelectedReads() {
        if (released) return
        released = true
        release.resolve()
        await stopWatching?.()
      }
      try {
        await observeStandardStreams(page, conversationId)
        stopWatching = await observeSelectedApprovalReads(page, snapshotUrl, inspectSelection)
        await page.goto(`/agents/${setup.agentId}/conversations/${conversationId}`)
        const form = page.locator('form[data-moldy-thread-composer]').last()
        const composer = form.locator('textarea[data-moldy-composer-input="true"]')
        await composer.fill('E2E_ASK_USER_FRUIT')
        await expect(form.locator('button[type="submit"]')).toBeEnabled({ timeout: 30_000 })
        const first = await standardCommand(
          page,
          conversationId,
          () => composer.press('Enter'),
          'interrupt',
          undefined,
          (slots) => {
            const declared = z.tuple([slot]).safeParse(slots)
            observations.terminalReason = reasonLabel(
              declared.success ? declared.data[0].reason : undefined,
            )
            terminalQuestionMatches =
              declared.success && matchingQuestion(declared.data[0], profile)
          },
        )
        expect(first.body.resume ?? []).toEqual([])
        await expect.poll(() => held.length, { timeout: 30_000 }).toBeGreaterThan(0)
        const pending = held[0]
        observations.sameSlot =
          first.verified.interruptIds.length === 1 &&
          first.verified.interruptIds[0] === pending.interrupts[0].id
        observations.sameRun = first.body.runId === pending.interruptRunId
        observations.sameActionContract =
          terminalQuestionMatches &&
          observations.actionable &&
          observations.terminalReason === observations.lastSnapshotReason
        expect(observations.sameActionContract).toBe(true)
        expect(observations.sameRun).toBe(true)
        expect(observations.sameSlot).toBe(true)
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

        observations.resumePostsBeforeRelease = observations.resumePosts
        observations.lockedBeforeRelease = true
        expect(observations.resumePostsBeforeRelease).toBe(0)
        await releaseSelectedReads()
        const apple = card.getByRole('option', { name: /사과/ })
        await expect(apple).toBeEnabled()
        await apple.press('Space')
        await expect(apple).toHaveAttribute('aria-selected', 'true')
        const confirm = card.getByRole('button', { name: /선택 확인|Confirm/ })
        await expect(confirm).toBeEnabled()
        const [resumed, submitted] = await Promise.all([
          standardCommand(page, conversationId, () => confirm.click(), 'success', finalAnswer),
          page.waitForRequest(
            (req) => req.method() === 'POST' && req.url() === api + '/ag-ui/runs',
          ),
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
              observations.completedResumeRuns = runs.filter(
                (run) =>
                  run.parent_run_id === pending.physicalInterruptRunId &&
                  run.source === 'resume' &&
                  run.status === 'completed',
              ).length
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
            .filter(
              (message) => message.type === 'tool' && message.tool_call_id === savedCalls[0].id,
            )
            .map((message) => message.content),
        ).toEqual([selection])
        const answers = messages.filter(
          (message) =>
            message.type === 'ai' && textContentFromMessageContent(message.content) === finalAnswer,
        )
        observations.savedAnswers = answers.length
        expect(observations.savedAnswers).toBe(1)
        expect(messages.filter((message) => message.id === answers[0].id)).toHaveLength(1)
        expect(observations.resumePosts).toBe(1)
        expect(writes).toEqual(['ag-ui', 'ag-ui'])
        observations.effectVerified = true
      } finally {
        await releaseSelectedReads()
        await testInfo.attach('approval-readiness', {
          body: Buffer.from(
            JSON.stringify(observations, (_key, value: unknown) =>
              typeof value === 'number' && _key !== 'lastHttpStatus' ? Math.min(value, 200) : value,
            ),
          ),
          contentType: 'application/json',
        })
        await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.agentId}`, setup.csrfHeaders)
      }
    },
  )
}
