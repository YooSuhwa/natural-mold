import { describe, expect, it } from 'vitest'
import {
  buildLiveRunSummary,
  buildPersistedRunSummary,
  messageIdBatchFor,
  runIdForMessage,
  runIdFromMessageId,
} from '@/lib/chat/run-summary-model'
import type { ConversationRunMetrics } from '@/lib/types'

const METRICS: ConversationRunMetrics = {
  terminal_state: 'completed',
  elapsed_ms: 2_450,
  ttft_ms: 300,
  generation_ms: 1_900,
  tokens_per_second: 12.5,
  prompt_tokens: 100,
  completion_tokens: 50,
  cache_creation_tokens: 0,
  cache_read_tokens: 20,
  estimated_cost: 0.012,
  usage_complete: true,
  root_tool_calls: 1,
  descendant_tool_calls: 2,
  root_subagent_calls: 1,
  descendant_subagent_calls: 1,
  activity_json: [
    { kind: 'tool', namespace: [], call_id: 'root-tool', name: 'search', elapsed_ms: 400 },
    {
      kind: 'subagent',
      namespace: ['researcher'],
      call_id: 'child-1',
      name: 'researcher',
      elapsed_ms: 900,
    },
  ],
  activity_truncated: false,
}

describe('run summary model', () => {
  it('preserves persisted root and descendant counts plus activity order', () => {
    const summary = buildPersistedRunSummary('run-1', 'completed', METRICS)

    expect(summary).toMatchObject({
      runId: 'run-1',
      elapsedMs: 2_450,
      rootToolCalls: 1,
      descendantToolCalls: 2,
      rootSubagentCalls: 1,
      descendantSubagentCalls: 1,
    })
    expect(summary.activities.map((activity) => activity.callId)).toEqual(['root-tool', 'child-1'])
  })

  it('keeps historical unavailable values unknown instead of turning them into zero', () => {
    const summary = buildPersistedRunSummary('run-old', 'completed', null)

    expect(summary).toMatchObject({
      elapsedMs: null,
      rootToolCalls: null,
      descendantToolCalls: null,
      rootSubagentCalls: null,
      descendantSubagentCalls: null,
    })
    expect(summary.activities).toEqual([])
  })

  it('does not sum a partially unknown count', () => {
    const summary = buildPersistedRunSummary('run-partial', 'canceled', {
      ...METRICS,
      descendant_tool_calls: null,
    })

    expect(summary.rootToolCalls).toBe(1)
    expect(summary.descendantToolCalls).toBeNull()
  })

  it('selects only the newest live run and never leaks prior-run totals', () => {
    const summary = buildLiveRunSummary(
      [
        {
          id: 'old-tool',
          runId: 'run-old',
          kind: 'tool',
          status: 'complete',
          title: 'old',
          namespace: [],
          toolCallId: 'old-call',
          startedAt: '2026-09-06T00:00:00.000Z',
        },
        {
          id: 'new-subagent',
          runId: 'run-new',
          kind: 'subagent',
          status: 'running',
          title: 'new',
          namespace: ['new'],
          toolCallId: 'new-call',
          startedAt: '2026-09-06T00:00:03.000Z',
        },
      ],
      5_000,
    )

    expect(summary).toMatchObject({
      runId: 'run-new',
      rootToolCalls: 0,
      descendantToolCalls: 0,
      rootSubagentCalls: 0,
      descendantSubagentCalls: 1,
    })
    expect(summary?.activities.map((activity) => activity.callId)).toEqual(['new-call'])
  })

  it('uses the durable trace link for persisted messages and exact ids for terminal notices', () => {
    const runId = 'ed9e5844-0269-4b0f-9325-5aebe93ac1d2'
    const messageId = '4722fbe8-b983-52ac-8ce0-9329a8f4d453'
    const links = [{ message_id: messageId, run_id: runId }]

    expect(runIdForMessage(links, messageId)).toBe(runId)
    expect(runIdFromMessageId(`moldy-failed-${runId}`)).toBe(runId)
    expect(runIdFromMessageId(`canceled-${runId}`)).toBe(runId)
    expect(runIdForMessage([], messageId)).toBeNull()
  })

  it('builds stable bounded batches containing the requested message', () => {
    const ids = Array.from(
      { length: 52 },
      (_, index) => `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
    )

    const batch = messageIdBatchFor(ids[51], [...ids].reverse())

    expect(batch).toHaveLength(2)
    expect(batch).toEqual(ids.slice(50))
  })

  it('keeps raw runtime ids, removes only known turn suffixes, and dedupes the batch', () => {
    const rawMessageId = 'lc_run--final'
    const turnMessageId = `${rawMessageId}::moldy-turn-2`

    expect(
      messageIdBatchFor(turnMessageId, [rawMessageId, turnMessageId, 'lc_run--other']),
    ).toEqual(['lc_run--final', 'lc_run--other'])
    expect(
      runIdForMessage(
        [{ message_id: rawMessageId, run_id: 'ed9e5844-0269-4b0f-9325-5aebe93ac1d2' }],
        turnMessageId,
      ),
    ).toBe('ed9e5844-0269-4b0f-9325-5aebe93ac1d2')
  })
})
