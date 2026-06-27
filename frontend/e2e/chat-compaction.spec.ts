import type { APIRequestContext, Page } from '@playwright/test'
import {
  API_BASE,
  apiDeleteOk,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  test,
  type CsrfHeaders,
} from './fixtures'
import { sendMessage, stringField, waitForActiveRun, waitForRunStatus } from './langgraph-v3-helpers'

// Auto-compaction marker E2E (dev-plan-context-compaction-marker.md).
//
// deepagents' SummarizationMiddleware compacts the message history once the
// token count crosses 85% of ``model.profile["max_input_tokens"]`` — which Moldy
// sources from ``models.context_window`` (Phase 0). To make compaction fire
// deterministically in CI we register a *dedicated* keyless scripted model with a
// tiny context window (50 tokens → ~42 token threshold) and bind a throwaway
// agent to it. A couple of long warm-up turns push the history past the threshold,
// so the next turn compacts. The model is unique per run (no mutation of the
// shared seeded scripted model), so this stays isolated even under parallel
// workers.
const SCRIPTED_PROVIDER = 'e2e_scripted'
const COMPACTION_CONTEXT_WINDOW = 50
// ``E2E_SLOW_STREAM`` makes the scripted model stream its answer in ~0.75s chunks
// (≈4.5s total), keeping the run activity strip mounted long enough to observe the
// transient compaction pill before the run finishes.
const SLOW_STREAM_MARKER = 'E2E_SLOW_STREAM'
const CONTEXT_PADDING = '이것은 컨텍스트를 채우기 위한 아주 긴 질문입니다 '.repeat(30)
// Same i18n strings the components render (frontend/messages/ko.json):
//  - chat.activity.compaction  → run activity strip pill (running + complete)
//  - chat.compaction.summary   → permanent marker on the compacted turn
const COMPACTION_RUNNING_TEXT = '이전 대화를 압축하는 중'
const COMPACTION_SUMMARY_TEXT = '이전 대화를 요약해 컨텍스트를 정리했어요'
// The scripted model's generic reply (app/agent_runtime/e2e_scripted_model.py).
const SCRIPTED_GENERIC_REPLY = 'E2E scripted document model is ready.'

interface CompactionSetup {
  readonly agentId: string
  readonly conversationId: string
  readonly modelId: string
  readonly csrfHeaders: CsrfHeaders
}

async function createSmallContextScriptedModel(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
): Promise<string> {
  const unique = Date.now()
  const model = await apiPostJson(request, `${API_BASE}/api/models`, csrfHeaders, {
    provider: SCRIPTED_PROVIDER,
    model_name: `compaction-scripted-${unique}`,
    display_name: `E2E Compaction Scripted ${unique}`,
    context_window: COMPACTION_CONTEXT_WINDOW,
    cost_per_input_token: 0,
    cost_per_output_token: 0,
    supports_function_calling: true,
    input_modalities: ['text'],
    output_modalities: ['text'],
    source: 'manual',
    is_visible: true,
  })
  if (!isRecord(model)) throw new Error('compaction model create did not return an object')
  return stringField(model, 'id', 'compaction model')
}

async function setupCompactionAgent(request: APIRequestContext): Promise<CompactionSetup> {
  const csrfHeaders = await loginApi(request)
  const modelId = await createSmallContextScriptedModel(request, csrfHeaders)
  const unique = Date.now()
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name: `E2E Compaction Agent ${unique}`,
    description: 'Auto-compaction marker E2E fixture (tiny context window).',
    system_prompt: 'You are a helpful assistant. Answer concisely.',
    model_id: modelId,
    tool_ids: [],
    mcp_tool_ids: [],
    skill_ids: [],
    sub_agent_ids: [],
    middleware_configs: [],
  })
  if (!isRecord(agent)) throw new Error('compaction agent create did not return an object')
  const agentId = stringField(agent, 'id', 'compaction agent')
  const conversation = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${agentId}/conversations`,
    csrfHeaders,
    { title: 'Auto-compaction marker E2E' },
  )
  if (!isRecord(conversation)) throw new Error('conversation create did not return an object')
  return {
    agentId,
    conversationId: stringField(conversation, 'id', 'conversation'),
    modelId,
    csrfHeaders,
  }
}

// Warm-up turns use the scripted model's instant generic reply, which finishes
// faster than ``/runs/active`` can be polled — so completion is observed via the
// UI (the Nth identical reply has rendered) rather than the run-status API.
async function sendAndAwaitReply(page: Page, text: string, expectedReplyCount: number): Promise<void> {
  await sendMessage(page, text)
  await expect
    .poll(() => page.getByText(SCRIPTED_GENERIC_REPLY).count(), {
      timeout: 60_000,
      intervals: [250, 500, 1000],
    })
    .toBeGreaterThanOrEqual(expectedReplyCount)
}

test.describe('Auto-compaction marker', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  test('shows the transient 압축 중 pill and the permanent compaction summary marker', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(150_000)
    const setup = await setupCompactionAgent(request)

    try {
      await page.goto(`/agents/${setup.agentId}/conversations/${setup.conversationId}`)

      // Warm up the thread so the history exceeds the tiny 50-token window.
      await sendAndAwaitReply(page, `${CONTEXT_PADDING} [warmup 1]`, 1)
      await sendAndAwaitReply(page, `${CONTEXT_PADDING} [warmup 2]`, 2)

      // Capture turn: deepagents compacts before answering, then slow-streams.
      await sendMessage(page, `${SLOW_STREAM_MARKER} ${CONTEXT_PADDING} [capture]`)
      // Grab the run while it is still active so the slow stream can't finish
      // before we start asserting on the (transient) pill.
      const captureRunId = await waitForActiveRun(request, setup.conversationId)

      // (a) Transient "압축 중…" pill in the run activity strip.
      const compactionPill = page
        .getByTestId('run-activity-strip')
        .locator('[data-kind="compaction"]')
      await expect(compactionPill).toBeVisible({ timeout: 30_000 })
      await expect(compactionPill).toContainText(COMPACTION_RUNNING_TEXT)

      // Let the slow stream finish.
      await waitForRunStatus(request, setup.conversationId, captureRunId, 'completed')

      // (b) Permanent compaction summary marker on the compacted assistant turn.
      const compactionSummary = page.getByTestId('compaction-summary').first()
      await expect(compactionSummary).toBeVisible({ timeout: 30_000 })
      await expect(compactionSummary).toContainText(COMPACTION_SUMMARY_TEXT)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      // Best-effort cleanup — by the time finally runs the API request context can
      // already be torn down with the page ("browser has been closed"), so swallow
      // teardown errors rather than flaking a green body. The throwaway DB is wiped
      // per run regardless.
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.agentId}`, setup.csrfHeaders).catch(
        () => {},
      )
      await apiDeleteOk(request, `${API_BASE}/api/models/${setup.modelId}`, setup.csrfHeaders).catch(
        () => {},
      )
    }
  })
})
