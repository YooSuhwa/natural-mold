import type { APIRequestContext, Page } from '@playwright/test'
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
import {
  sendMessage,
  sendMessageForRun,
  stringField,
  waitForRunStatus,
} from './langgraph-v3-helpers'

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
const SCRIPTED_SLOW_REPLY = 'E2E slow stream completed after detached navigation.'

type SummarizationPolicy =
  | { readonly mode: 'auto' }
  | { readonly mode: 'preset'; readonly preset: 'balanced_context_v1' }

interface CompactionSetup {
  readonly agentId: string
  readonly conversationId: string
  readonly modelId: string
  readonly csrfHeaders: CsrfHeaders
}

function runtimePolicy(summarization: SummarizationPolicy) {
  return {
    version: 1,
    filesystem: { mode: 'artifact_write' as const },
    todo: { enabled: true },
    summarization,
  }
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

async function setupCompactionAgent(
  request: APIRequestContext,
  summarization: SummarizationPolicy,
): Promise<CompactionSetup> {
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
    runtime_policy: runtimePolicy(summarization),
  })
  if (!isRecord(agent)) throw new Error('compaction agent create did not return an object')
  const agentId = stringField(agent, 'id', 'compaction agent')
  expect(agent.runtime_policy_source).toBe('stored')
  expect(agent.runtime_policy).toEqual(runtimePolicy(summarization))
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
async function sendAndAwaitReply(
  page: Page,
  text: string,
  expectedReplyCount: number,
): Promise<void> {
  await sendMessage(page, text)
  await expect
    .poll(() => page.getByText(SCRIPTED_GENERIC_REPLY).count(), {
      timeout: 60_000,
      intervals: [250, 500, 1000],
    })
    .toBeGreaterThanOrEqual(expectedReplyCount)
}

async function expectStoredRunPolicy(
  request: APIRequestContext,
  conversationId: string,
  runId: string,
): Promise<void> {
  const run = await apiGetJson(
    request,
    `${API_BASE}/api/conversations/${conversationId}/runs/${runId}`,
  )
  if (!isRecord(run)) throw new Error('compaction run did not return an object')
  expect(run.runtime_policy_source).toBe('stored')
  expect(run.runtime_policy_version).toBe(1)
  expect(run.runtime_policy_hash).toMatch(/^[a-f0-9]{64}$/)
}

test.describe('Auto-compaction marker', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  async function runStoredCompactionScenario(
    page: Page,
    request: APIRequestContext,
    errors: {
      readonly console: readonly string[]
      readonly network: readonly string[]
      readonly page: readonly string[]
    },
    summarization: SummarizationPolicy,
  ): Promise<void> {
    const setup = await setupCompactionAgent(request, summarization)

    try {
      await page.goto(`/agents/${setup.agentId}/conversations/${setup.conversationId}`)

      // Warm up the thread so the history exceeds the tiny 50-token window.
      await sendAndAwaitReply(page, `${CONTEXT_PADDING} [warmup 1]`, 1)
      await sendAndAwaitReply(page, `${CONTEXT_PADDING} [warmup 2]`, 2)

      // Capture the first compaction while the slow stream keeps the transient
      // activity strip visible. ``run.start`` supplies the accepted id even when
      // the backend completes a fast run before active-run polling begins.
      const firstRunId = await sendMessageForRun(
        page,
        setup.conversationId,
        `${SLOW_STREAM_MARKER} ${CONTEXT_PADDING} [capture 1]`,
      )

      const compactionPill = page
        .getByTestId('run-activity-strip')
        .locator('[data-kind="compaction"]')
      await expect(compactionPill).toBeVisible({ timeout: 30_000 })
      await expect(compactionPill).toContainText(COMPACTION_RUNNING_TEXT)

      await waitForRunStatus(request, setup.conversationId, firstRunId, 'completed')
      await expectStoredRunPolicy(request, setup.conversationId, firstRunId)
      await expect(page.getByText(SCRIPTED_SLOW_REPLY, { exact: true })).toBeVisible({
        timeout: 30_000,
      })

      // A subsequent long turn must compact again. The permanently projected
      // Korean marker is intentionally the only UI surface; upstream event data
      // remains transport-only.
      const secondRunId = await sendMessageForRun(
        page,
        setup.conversationId,
        `${CONTEXT_PADDING} [capture 2]`,
      )
      await waitForRunStatus(request, setup.conversationId, secondRunId, 'completed')
      await expectStoredRunPolicy(request, setup.conversationId, secondRunId)
      await expect
        .poll(() => page.getByTestId('compaction-summary').count(), {
          timeout: 30_000,
          intervals: [250, 500, 1000],
        })
        .toBeGreaterThanOrEqual(2)
      await expect(page.getByTestId('compaction-summary').first()).toContainText(
        COMPACTION_SUMMARY_TEXT,
      )
      await expect(page.getByText(SCRIPTED_GENERIC_REPLY, { exact: true })).toHaveCount(3)

      // A normal interaction after compaction must still finish safely rather
      // than leaving the conversation at a summary/checkpoint boundary.
      const followUpRunId = await sendMessageForRun(
        page,
        setup.conversationId,
        'E2E post-compaction follow-up',
      )
      await waitForRunStatus(request, setup.conversationId, followUpRunId, 'completed')
      await expectStoredRunPolicy(request, setup.conversationId, followUpRunId)
      await expect(page.getByText(SCRIPTED_GENERIC_REPLY, { exact: true })).toHaveCount(4)

      expect(await page.locator('body').textContent()).not.toContain('moldy.compaction')
      expect(await page.locator('body').textContent()).not.toContain('offload_path')
      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
      expect(errors.page).toEqual([])
    } finally {
      // The fixture is dedicated to this test, so deleting the parent agent also
      // removes its conversations. The isolated lane's throwaway database is an
      // independent final cleanup boundary if a browser teardown interrupts this.
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.agentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/models/${setup.modelId}`, setup.csrfHeaders)
    }
  }

  test('stored auto policy shows bounded compaction projection for two compactions', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(210_000)
    await runStoredCompactionScenario(page, request, errors, { mode: 'auto' })
  })

  test('stored balanced preset shows bounded compaction projection for two compactions', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(210_000)
    await runStoredCompactionScenario(page, request, errors, {
      mode: 'preset',
      preset: 'balanced_context_v1',
    })
  })
})
