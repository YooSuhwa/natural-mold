import type { APIRequestContext, Page } from '@playwright/test'
import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiPostJson,
  expect,
  failWithBody,
  isRecord,
  loginApi,
  test,
  type CsrfHeaders,
} from './fixtures'
import {
  approveExecuteInSkill,
  expectFinalTextVisible,
  records,
  sendMessage,
  setupLangGraphV3Agent,
  stringField,
  waitForActiveRun,
  waitForArtifact,
  waitForRunStatus,
} from './langgraph-v3-helpers'
import { waitForThreadStateText } from './langgraph-v3-state-helpers'

const SCRIPTED_PROVIDER = 'e2e_scripted'
const SCRIPTED_MODEL = 'document-artifact-scripted'
const SECRET_TOOL_ARGS_REQUEST = 'secret_tool_arg=true'
const SECRET_TOOL_ARG_VALUE = 'moldy-e2e-secret-token-should-not-persist'
const TOKEN_USAGE_MARKER = 'E2E_TOKEN_USAGE_STREAM'
const TOKEN_USAGE_RESPONSE = 'E2E token usage isolated conversation response.'
const SLOW_STREAM_RESPONSE = 'E2E slow stream completed after detached navigation.'
const REPORT_FILE = 'moldy-langgraph-v3-report.md'

interface ScriptedAgentSetup {
  readonly agentId: string
  readonly csrfHeaders: CsrfHeaders
}

interface MessageRow {
  readonly role: string
  readonly content: string
  readonly branchIndex: number | null
  readonly branchTotal: number | null
  readonly siblingCheckpointIds: readonly string[]
}

async function scriptedModelId(request: APIRequestContext): Promise<string> {
  const models = records(await apiGetJson(request, `${API_BASE}/api/models`), 'models')
  const model = models.find(
    (row) => row.provider === SCRIPTED_PROVIDER && row.model_name === SCRIPTED_MODEL,
  )
  if (!model) throw new Error('E2E scripted model is not seeded')
  return stringField(model, 'id', 'scripted model')
}

async function createScriptedAgent(
  request: APIRequestContext,
  name: string,
): Promise<ScriptedAgentSetup> {
  const csrfHeaders = await loginApi(request)
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name,
    system_prompt: 'You are a deterministic LangGraph v3 regression fixture.',
    model_id: await scriptedModelId(request),
    tool_ids: [],
    mcp_tool_ids: [],
    skill_ids: [],
    sub_agent_ids: [],
    middleware_configs: [],
  })
  if (!isRecord(agent)) throw new Error('agent create did not return an object')
  return { agentId: stringField(agent, 'id', 'agent'), csrfHeaders }
}

async function createConversation(
  request: APIRequestContext,
  setup: ScriptedAgentSetup,
  title: string,
): Promise<string> {
  const conversation = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${setup.agentId}/conversations`,
    setup.csrfHeaders,
    { title },
  )
  if (!isRecord(conversation)) throw new Error('conversation create did not return an object')
  return stringField(conversation, 'id', 'conversation')
}

async function apiPostOk(
  request: APIRequestContext,
  url: string,
  csrfHeaders: CsrfHeaders,
  data?: Record<string, unknown>,
): Promise<void> {
  const response = await request.post(url, { headers: csrfHeaders, data })
  if (!response.ok()) await failWithBody(`POST ${url}`, response)
}

async function waitRunIdle(request: APIRequestContext, conversationId: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const run = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${conversationId}/runs/active`,
        )
        return run === null ? null : 'active'
      },
      { timeout: 60_000, intervals: [500, 1000, 2000] },
    )
    .toBe(null)
}

function textField(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  return typeof value === 'string' ? value : ''
}

function numberField(record: Record<string, unknown>, key: string): number | null {
  const value = record[key]
  return typeof value === 'number' ? value : null
}

function stringArrayField(record: Record<string, unknown>, key: string): readonly string[] {
  const value = record[key]
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : []
}

function messageRows(envelope: unknown): MessageRow[] {
  if (!isRecord(envelope)) throw new Error('messages response did not return an object')
  const rawMessages = envelope.messages
  if (!Array.isArray(rawMessages) || !rawMessages.every(isRecord)) {
    throw new Error('messages response did not include message records')
  }
  return rawMessages.map((message) => ({
    role: textField(message, 'role'),
    content: textField(message, 'content'),
    branchIndex: numberField(message, 'branch_index'),
    branchTotal: numberField(message, 'branch_total'),
    siblingCheckpointIds: stringArrayField(message, 'sibling_checkpoint_ids'),
  }))
}

async function loadMessages(
  request: APIRequestContext,
  conversationId: string,
): Promise<MessageRow[]> {
  return messageRows(
    await apiGetJson(request, `${API_BASE}/api/conversations/${conversationId}/messages`),
  )
}

async function waitForMessage(
  request: APIRequestContext,
  conversationId: string,
  role: string,
  contentNeedle: string,
): Promise<void> {
  await expect
    .poll(
      async () => countMessages(await loadMessages(request, conversationId), role, contentNeedle),
      { timeout: 60_000, intervals: [500, 1000, 2000] },
    )
    .toBe(1)
}

function branchAssistantMessage(messages: readonly MessageRow[]): MessageRow | null {
  return (
    messages.find(
      (message) =>
        message.role === 'assistant' &&
        message.branchTotal === 2 &&
        message.siblingCheckpointIds.length === 2,
    ) ?? null
  )
}

async function waitForAssistantBranch(
  request: APIRequestContext,
  conversationId: string,
  branchIndex: number,
): Promise<MessageRow> {
  let branchMessage: MessageRow | null = null
  await expect
    .poll(
      async () => {
        branchMessage = branchAssistantMessage(await loadMessages(request, conversationId))
        return branchMessage?.branchIndex ?? null
      },
      { timeout: 60_000, intervals: [500, 1000, 2000] },
    )
    .toBe(branchIndex)
  if (!branchMessage) throw new Error('assistant branch metadata was not available')
  return branchMessage
}

function countMessages(
  messages: readonly MessageRow[],
  role: string,
  contentNeedle: string,
): number {
  return messages.filter(
    (message) => message.role === role && message.content.includes(contentNeedle),
  ).length
}

function tokenUsageButtons(page: Page) {
  return page.locator('main').getByRole('button', {
    name: /토큰 사용량 보기|Token Usage|Toggle Aria/,
  })
}

async function expectTokenUsageTotal(page: Page, total: string): Promise<void> {
  const button = tokenUsageButtons(page).last()
  await expect(button).toBeVisible({ timeout: 20_000 })
  await expect(button).toContainText(total)
}

function stringifyJson(value: unknown, label: string): string {
  const serialized = JSON.stringify(value)
  if (typeof serialized !== 'string') {
    throw new Error(`${label} could not be serialized`)
  }
  return serialized
}

function firstDebugTraceId(value: unknown): string {
  if (!isRecord(value)) throw new Error('debug traces response did not return an object')
  const traces = records(value.traces, 'debug traces')
  const firstTrace = traces[0]
  if (!firstTrace) throw new Error('debug traces response was empty')
  return stringField(firstTrace, 'trace_id', 'debug trace')
}

test.describe('LangGraph v3 regression coverage', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  test('restores an interrupted HITL run after reload and redacts protocol traces/share data', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await sendMessage(
        page,
        `E2E_LANGGRAPH_V3 ${SECRET_TOOL_ARGS_REQUEST} subagent=${setup.childRuntimeName}`,
      )

      const runId = await waitForActiveRun(request, setup.conversationId)
      await waitForRunStatus(request, setup.conversationId, runId, 'interrupted')
      await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
        timeout: 30_000,
      })
      await expect(page.locator('body')).not.toContainText(SECRET_TOOL_ARG_VALUE)

      await page.reload()
      await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
        timeout: 30_000,
      })
      await expect(page.locator('body')).not.toContainText(SECRET_TOOL_ARG_VALUE)

      await approveExecuteInSkill(page)
      await waitForArtifact(request, setup.conversationId, REPORT_FILE)
      await expectFinalTextVisible(page)
      await waitRunIdle(request, setup.conversationId)

      const traces = await apiGetJson(
        request,
        `${API_BASE}/api/conversations/${setup.conversationId}/traces`,
      )
      const tracesText = stringifyJson(traces, 'conversation traces')
      expect(tracesText).not.toContain(SECRET_TOOL_ARG_VALUE)
      expect(tracesText).toContain('<redacted>')

      const debugList = await apiGetJson(
        request,
        `${API_BASE}/api/conversations/${setup.conversationId}/debug/traces`,
      )
      const debugDetail = await apiGetJson(
        request,
        `${API_BASE}/api/conversations/${setup.conversationId}/debug/traces/${firstDebugTraceId(
          debugList,
        )}`,
      )
      const debugText = stringifyJson(debugDetail, 'debug trace detail')
      expect(debugText).not.toContain(SECRET_TOOL_ARG_VALUE)

      const share = await apiPostJson(
        request,
        `${API_BASE}/api/conversations/${setup.conversationId}/share`,
        setup.csrfHeaders,
      )
      if (!isRecord(share)) throw new Error('share create did not return an object')
      const sharedSnapshot = await apiGetJson(
        request,
        `${API_BASE}/api/shares/${stringField(share, 'share_token', 'share')}`,
      )
      const shareText = stringifyJson(sharedSnapshot, 'shared conversation')
      expect(shareText).not.toContain(SECRET_TOOL_ARG_VALUE)
      expect(shareText).toContain('<redacted>')

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('keeps the delegated subagent result inline after reload and HITL resume', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)
    // G10: the subagent pill shows the human-readable child agent name, not the
    // raw runtime name (`agent_<8hex>`). The backend ships a runtime_name ->
    // display_name map over the `moldy.subagent_names` side-channel and the card
    // substitutes it at the display layer; the SDK's raw `subagent_type` snapshot
    // is untouched. This chip must stay the display name across live / reload /
    // HITL resume / pure-hydration reload.
    const subagentChip = page.getByText(setup.childName).first()
    // The delegated subagent's scoped final report. It renders inside the
    // subagent card body, so it is only visible when that card is expanded —
    // exactly the state the reload-hydration regression collapsed.
    const subagentResult = page.getByText('E2E subagent scoped result ready.').first()

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await sendMessage(page, `E2E_LANGGRAPH_V3 subagent=${setup.childRuntimeName}`)

      const runId = await waitForActiveRun(request, setup.conversationId)
      await waitForRunStatus(request, setup.conversationId, runId, 'interrupted')
      await expect(page.getByText('Collect LangGraph v3 runtime evidence').first()).toBeVisible({
        timeout: 30_000,
      })
      // Live (before any reload): the subagent card is expanded with its result.
      await expect(subagentChip).toBeVisible()
      await expect(subagentResult).toBeVisible()

      // The subagent finished before the HITL interrupt, so on reload its
      // discovery snapshot is seeded asynchronously from getState. Regression:
      // CollapsiblePill read `defaultExpanded` only at mount, so the card mounted
      // collapsed and never re-expanded once the snapshot landed — hiding the
      // scoped result after reload. Wait for the subagent state to persist first
      // so getState actually carries it.
      await waitForThreadStateText(
        request,
        setup.conversationId,
        'Render delegated subagent progress',
      )
      await page.reload()
      await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
        timeout: 30_000,
      })
      await approveExecuteInSkill(page)
      await waitForArtifact(request, setup.conversationId, REPORT_FILE)
      await expectFinalTextVisible(page)

      // After reload + resume the scoped result is back inline in the transcript
      // (no right-rail click) — this is the assertion the fix restores.
      await expect(subagentChip).toBeVisible()
      await expect(subagentResult).toBeVisible({ timeout: 30_000 })

      // A second reload of the now-completed thread exercises the pure hydration
      // path (no live stream at all): the subagent card must rehydrate expanded.
      await page.reload()
      await expectFinalTextVisible(page)
      await expect(subagentChip).toBeVisible()
      await expect(subagentResult).toBeVisible({ timeout: 30_000 })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('keeps the selected regenerated branch after reload', async ({ page, request, errors }) => {
    test.setTimeout(120_000)
    const setup = await createScriptedAgent(request, `E2E v3 Branch ${Date.now()}`)

    try {
      const conversationId = await createConversation(request, setup, 'v3 branch persistence')
      await page.goto(`/agents/${setup.agentId}/conversations/${conversationId}`)
      await sendMessage(page, 'E2E branch persistence first turn')
      await waitForMessage(
        request,
        conversationId,
        'assistant',
        'E2E scripted document model is ready.',
      )
      await page.reload()
      await waitRunIdle(request, conversationId)

      await page.getByRole('button', { name: '재생성' }).first().click()
      const regenerated = await waitForAssistantBranch(request, conversationId, 1)
      await waitRunIdle(request, conversationId)

      const previousCheckpointId = regenerated.siblingCheckpointIds[0]
      if (!previousCheckpointId) throw new Error('previous branch checkpoint id was missing')
      await apiPostOk(
        request,
        `${API_BASE}/api/conversations/${conversationId}/messages/switch-branch`,
        setup.csrfHeaders,
        { checkpoint_id: previousCheckpointId },
      )
      await waitForAssistantBranch(request, conversationId, 0)

      await page.reload()
      await expect(page.getByText('1/2').first()).toBeVisible({ timeout: 30_000 })
      await expect(page.getByText('E2E scripted document model is ready.').first()).toBeVisible()

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.agentId}`, setup.csrfHeaders)
    }
  })

  test('does not duplicate messages when a slow stream reconnects after reload', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(120_000)
    const setup = await createScriptedAgent(request, `E2E v3 Reconnect ${Date.now()}`)

    try {
      const conversationId = await createConversation(request, setup, 'v3 reconnect dedupe')
      const prompt = 'E2E_SLOW_STREAM reconnect dedupe'

      await page.goto(`/agents/${setup.agentId}/conversations/${conversationId}`)
      await sendMessage(page, prompt)
      await expect(page.locator(`[data-moldy-run-spinner="${conversationId}"]`)).toBeVisible({
        timeout: 10_000,
      })

      await page.reload()
      const restoredSpinner = page.locator(`[data-moldy-run-spinner="${conversationId}"]`)
      const restoredFinalMessage = page.getByText(SLOW_STREAM_RESPONSE).first()
      await expect
        .poll(
          async () =>
            (await restoredSpinner.isVisible().catch(() => false)) ||
            (await restoredFinalMessage.isVisible().catch(() => false)),
          { timeout: 15_000 },
        )
        .toBe(true)
      await expect(restoredFinalMessage).toBeVisible({ timeout: 45_000 })
      await waitRunIdle(request, conversationId)

      const messages = await loadMessages(request, conversationId)
      expect(countMessages(messages, 'user', prompt)).toBe(1)
      expect(countMessages(messages, 'assistant', SLOW_STREAM_RESPONSE)).toBe(1)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.agentId}`, setup.csrfHeaders)
    }
  })

  test('resets token usage state when switching between conversations', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(120_000)
    const setup = await createScriptedAgent(request, `E2E v3 Usage ${Date.now()}`)

    try {
      const conversationA = await createConversation(request, setup, 'v3 usage A')
      const conversationB = await createConversation(request, setup, 'v3 usage B')

      await page.goto(`/agents/${setup.agentId}/conversations/${conversationA}`)
      await sendMessage(page, `${TOKEN_USAGE_MARKER} A`)
      await waitForMessage(request, conversationA, 'assistant', TOKEN_USAGE_RESPONSE)
      await page.reload()
      await expect(page.getByText(TOKEN_USAGE_RESPONSE).first()).toBeVisible({ timeout: 45_000 })
      await expectTokenUsageTotal(page, '42')
      await waitRunIdle(request, conversationA)

      await page
        .locator(
          `[data-chat-session-href="/agents/${setup.agentId}/conversations/${conversationB}"] a`,
        )
        .click()
      await expect(page).toHaveURL(
        new RegExp(`/agents/${setup.agentId}/conversations/${conversationB}$`),
      )
      await expect(tokenUsageButtons(page)).toHaveCount(0)

      await sendMessage(page, `${TOKEN_USAGE_MARKER} B`)
      await waitForMessage(request, conversationB, 'assistant', TOKEN_USAGE_RESPONSE)
      await page.reload()
      await expect(page.getByText(TOKEN_USAGE_RESPONSE).first()).toBeVisible({ timeout: 45_000 })
      await expect(tokenUsageButtons(page)).toHaveCount(1)
      await expectTokenUsageTotal(page, '42')
      await waitRunIdle(request, conversationB)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.agentId}`, setup.csrfHeaders)
    }
  })
})
