import type { APIRequestContext, Page, TestInfo } from '@playwright/test'

import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  test,
  type CsrfHeaders,
} from './fixtures'
import { records, sendMessageForRun, stringField, waitForRunStatus } from './langgraph-v3-helpers'

const SCRIPTED_PROVIDER = 'e2e_scripted'
const SCRIPTED_MODEL = 'document-artifact-scripted'
const TODO_POLICY_MARKER = 'E2E_RUNTIME_TODO_POLICY'
const TODO_POLICY_FINAL = 'E2E runtime Todo policy validation complete.'
const TODO_POLICY_TOOL_CALL_ID_PREFIX = 'call_e2e_runtime_todo_policy_turn'
const RUNTIME_POLICY_CAPTURE_VIEWPORTS = [375, 768, 1280] as const

interface TodoPolicyAgentSetup {
  readonly agentId: string
  readonly csrfHeaders: CsrfHeaders
}

interface RunPolicyEvidence {
  readonly hash: string
  readonly source: string
}

async function captureTodoAbsentState(page: Page, testInfo: TestInfo): Promise<void> {
  if (testInfo.project.name !== 'scripted-capture') return

  for (const width of RUNTIME_POLICY_CAPTURE_VIEWPORTS) {
    await page.setViewportSize({ width, height: 960 })
    const final = page.getByText(TODO_POLICY_FINAL, { exact: true })
    await final.scrollIntoViewIfNeeded()
    await expect(final).toBeInViewport()
    await expect(page.getByText('Plan', { exact: true })).toHaveCount(0)
    await page.screenshot({
      path: testInfo.outputPath(`todo-absent-${width}.png`),
      fullPage: false,
    })
  }
}

test.describe.configure({ mode: 'serial', retries: 0 })

function runtimePolicy(todoEnabled: boolean) {
  return {
    version: 1,
    filesystem: { mode: 'artifact_write' as const },
    todo: { enabled: todoEnabled },
    summarization: { mode: 'auto' as const },
  }
}

function todoPolicyToolCallId(markerTurn: number): string {
  return `${TODO_POLICY_TOOL_CALL_ID_PREFIX}_${markerTurn}`
}

function toolCallIdsForName(value: unknown, toolName: string): readonly string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => toolCallIdsForName(item, toolName))
  }
  if (!isRecord(value)) return []

  const ids: string[] = []
  if (Array.isArray(value.tool_calls)) {
    for (const toolCall of value.tool_calls) {
      if (isRecord(toolCall) && toolCall.name === toolName && typeof toolCall.id === 'string') {
        ids.push(toolCall.id)
      }
    }
  }
  for (const child of Object.values(value)) ids.push(...toolCallIdsForName(child, toolName))
  return ids
}

function toolResultsForCall(value: unknown, toolCallId: string): readonly string[] {
  if (Array.isArray(value)) return value.flatMap((item) => toolResultsForCall(item, toolCallId))
  if (!isRecord(value)) return []

  const results: string[] = []
  if (
    value.type === 'tool' &&
    value.tool_call_id === toolCallId &&
    typeof value.content === 'string'
  ) {
    results.push(value.content)
  }
  if (
    value.event === 'tool_call_result' &&
    isRecord(value.data) &&
    value.data.tool_call_id === toolCallId &&
    typeof value.data.result === 'string'
  ) {
    results.push(value.data.result)
  }
  for (const child of Object.values(value)) {
    results.push(...toolResultsForCall(child, toolCallId))
  }
  return results
}

async function scriptedModelId(request: APIRequestContext): Promise<string> {
  const models = records(await apiGetJson(request, `${API_BASE}/api/models`), 'models')
  const model = models.find(
    (row) => row.provider === SCRIPTED_PROVIDER && row.model_name === SCRIPTED_MODEL,
  )
  if (!model) throw new Error('E2E scripted model is not seeded')
  return stringField(model, 'id', 'scripted model')
}

async function createTodoPolicyAgent(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  name: string,
): Promise<TodoPolicyAgentSetup> {
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name,
    system_prompt: 'Run only the deterministic E2E runtime Todo policy sequence.',
    model_id: await scriptedModelId(request),
    tool_ids: [],
    mcp_tool_ids: [],
    skill_ids: [],
    sub_agent_ids: [],
    middleware_configs: [],
    runtime_policy: runtimePolicy(true),
  })
  if (!isRecord(agent)) throw new Error('agent create did not return an object')
  expect(agent.runtime_policy).toEqual(runtimePolicy(true))
  expect(agent.runtime_policy_source).toBe('stored')
  return { agentId: stringField(agent, 'id', 'agent'), csrfHeaders }
}

async function createConversation(
  request: APIRequestContext,
  setup: TodoPolicyAgentSetup,
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

async function updateTodoPolicy(
  request: APIRequestContext,
  setup: TodoPolicyAgentSetup,
  enabled: boolean,
): Promise<void> {
  const response = await request.put(`${API_BASE}/api/agents/${setup.agentId}`, {
    headers: setup.csrfHeaders,
    data: { runtime_policy: runtimePolicy(enabled) },
  })
  expect(response.ok()).toBe(true)
  const updated: unknown = await response.json()
  if (!isRecord(updated)) throw new Error('agent update did not return an object')
  expect(updated.runtime_policy).toEqual(runtimePolicy(enabled))
  expect(updated.runtime_policy_source).toBe('stored')
}

async function runPolicyEvidence(
  request: APIRequestContext,
  conversationId: string,
  runId: string,
): Promise<RunPolicyEvidence> {
  const run = await apiGetJson(
    request,
    `${API_BASE}/api/conversations/${conversationId}/runs/${runId}`,
  )
  if (!isRecord(run)) throw new Error('conversation run did not return an object')
  const hash = stringField(run, 'runtime_policy_hash', 'conversation run')
  const source = stringField(run, 'runtime_policy_source', 'conversation run')
  expect(run.runtime_policy_version).toBe(1)
  return { hash, source }
}

async function runtimeToolEvidence(
  request: APIRequestContext,
  conversationId: string,
): Promise<unknown> {
  const encodedConversationId = encodeURIComponent(conversationId)
  return apiGetJson(
    request,
    `${API_BASE}/api/conversations/${encodedConversationId}/langgraph/threads/${encodedConversationId}/state`,
  )
}

async function expectWriteTodosTrace(
  request: APIRequestContext,
  conversationId: string,
  expectedCallId: string,
  expected: boolean,
): Promise<void> {
  if (expected) {
    await expect
      .poll(
        async () => {
          const evidence = await runtimeToolEvidence(request, conversationId)
          const callIds = toolCallIdsForName(evidence, 'write_todos')
          return (
            callIds.includes(expectedCallId) &&
            toolResultsForCall(evidence, expectedCallId).length > 0
          )
        },
        {
          timeout: 20_000,
          intervals: [250, 500, 1000],
        },
      )
      .toBe(true)
  }
  const evidence = await runtimeToolEvidence(request, conversationId)
  const callIds = toolCallIdsForName(evidence, 'write_todos')
  const results = toolResultsForCall(evidence, expectedCallId)
  if (expected) {
    expect(callIds).toContain(expectedCallId)
    expect(results.length).toBeGreaterThan(0)
    return
  }
  expect(callIds).toEqual([])
  expect(results).toEqual([])
}

test.describe('stored runtime Todo policy E2E', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  test('keeps an enabled conversation snapshot after agent policy changes and omits Todo in a new off conversation', async ({
    page,
    request,
    errors,
  }, testInfo) => {
    test.setTimeout(120_000)
    const csrfHeaders = await loginApi(request)
    const setup = await createTodoPolicyAgent(request, csrfHeaders, `E2E Todo Policy ${Date.now()}`)

    try {
      const enabledConversationId = await createConversation(
        request,
        setup,
        'E2E Todo policy enabled snapshot',
      )
      await page.goto(`/agents/${setup.agentId}/conversations/${enabledConversationId}`)
      const enabledRunId = await sendMessageForRun(page, enabledConversationId, TODO_POLICY_MARKER)
      await waitForRunStatus(request, enabledConversationId, enabledRunId, 'completed')
      await expect(page.getByText(TODO_POLICY_FINAL, { exact: true })).toBeVisible()
      await expect(page.getByText('Plan', { exact: true })).toHaveCount(1)
      await expectWriteTodosTrace(request, enabledConversationId, todoPolicyToolCallId(1), true)
      const enabledRun = await runPolicyEvidence(request, enabledConversationId, enabledRunId)
      expect(enabledRun.source).toBe('stored')

      await updateTodoPolicy(request, setup, false)

      const resumedRunId = await sendMessageForRun(
        page,
        enabledConversationId,
        `${TODO_POLICY_MARKER} existing-snapshot`,
      )
      await waitForRunStatus(request, enabledConversationId, resumedRunId, 'completed')
      await expectWriteTodosTrace(request, enabledConversationId, todoPolicyToolCallId(2), true)
      const resumedRun = await runPolicyEvidence(request, enabledConversationId, resumedRunId)
      expect(resumedRun).toEqual(enabledRun)

      const disabledConversationId = await createConversation(
        request,
        setup,
        'E2E Todo policy disabled new conversation',
      )
      await page.goto(`/agents/${setup.agentId}/conversations/${disabledConversationId}`)
      const disabledRunId = await sendMessageForRun(
        page,
        disabledConversationId,
        TODO_POLICY_MARKER,
      )
      await waitForRunStatus(request, disabledConversationId, disabledRunId, 'completed')
      await expect(page.getByText(TODO_POLICY_FINAL, { exact: true })).toBeVisible()
      await expect(page.getByText('Plan', { exact: true })).toHaveCount(0)
      await expect(page.getByText('작업 목록', { exact: true })).toHaveCount(0)
      await captureTodoAbsentState(page, testInfo)
      await expectWriteTodosTrace(request, disabledConversationId, todoPolicyToolCallId(1), false)
      const disabledRun = await runPolicyEvidence(request, disabledConversationId, disabledRunId)
      expect(disabledRun.source).toBe('stored')
      expect(disabledRun.hash).not.toBe(enabledRun.hash)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
      expect(errors.page).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.agentId}`, csrfHeaders)
    }
  })
})
