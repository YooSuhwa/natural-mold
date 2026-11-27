import { describe, expect, it, vi } from 'vitest'
import { EventSchema } from '@ag-ui/core/schemas'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-types'
import { buildLiveRunSummary } from '@/lib/chat/run-summary-model'
import {
  createFixtureAgent,
  parallelEvents,
  physicalRunId,
} from '../../../../tests/ag-ui/chat-ui-fixtures'
import {
  mergeAgUiRunActivities,
  rebindAgUiNativeActivities,
  reduceAgUiNativeActivities,
} from '../activity-native-summary'

const run = 'physical-run'
const threadId = 'thread'
const runId = 'public-run'
const base = 1_800_000_000_000
function apply(events: unknown[], initial: readonly RunActivity[] = []) {
  return events.reduce<readonly RunActivity[]>(
    (current, event) => reduceAgUiNativeActivities(current, EventSchema.parse(event), run),
    initial,
  )
}
const textStart = {
  type: 'TEXT_MESSAGE_START',
  messageId: 'answer',
  role: 'assistant',
  timestamp: base,
}
const tool = (name: string, call: string, lane?: string) => ({
  type: 'TOOL_CALL_START',
  toolCallId: call,
  toolCallName: name,
  subagentRunId: lane,
  timestamp: base + 10,
})
const result = (call: string, lane?: string) => ({
  type: 'TOOL_CALL_RESULT',
  toolCallId: call,
  messageId: 'result-' + call,
  content: 'private output',
  subagentRunId: lane,
})
const child = (id: string, parent?: string) => ({
  type: 'SUBAGENT_STARTED',
  subagentRunId: id,
  name: 'researcher',
  parentToolCallId: 'dispatch',
  parentSubagentRunId: parent,
})

describe('public AG-UI run summary', () => {
  it('rebinds only locally generated rows and parent keys after a queued run is claimed', () => {
    const provisional = 'queue-"id"'
    const parent = 'child/' + provisional
    const nested = 'inner/' + provisional
    const call = 'tool/' + provisional
    const frames = [child(parent), child(nested, parent), tool('read', call, nested)]
    const early = frames.reduce<readonly RunActivity[]>(
      (rows, frame) => reduceAgUiNativeActivities(rows, frame, provisional),
      [],
    )
    const unrelated = { ...early[0], runId: 'another-run' }
    const opaque = { ...early[0], id: provisional, parentId: parent }
    const bound = rebindAgUiNativeActivities([...early, unrelated, opaque], provisional, run)
    const expected = frames.reduce<readonly RunActivity[]>(
      (rows, frame) => reduceAgUiNativeActivities(rows, frame, run),
      [],
    )
    expect(bound.slice(0, 3)).toEqual(expected)
    expect(bound[1].parentId).toBe(bound[0].id)
    expect(bound[2]).toMatchObject({
      toolCallId: call,
      namespace: [parent, nested],
      startedAt: early[2].startedAt,
    })
    expect(bound[3]).toBe(unrelated)
    expect(bound[4]).toBe(opaque)
    expect(rebindAgUiNativeActivities(bound, provisional, run)).toBe(bound)
    const finished = reduceAgUiNativeActivities(bound, result(call, nested), run)
    expect(finished[2].status).toBe('complete')
    const endedEarly = reduceAgUiNativeActivities(early, result(call, nested), provisional)
    const joined = rebindAgUiNativeActivities([...expected, ...endedEarly], provisional, run)
    expect(joined).toHaveLength(3)
    expect(joined[2].status).toBe('complete')
  })

  it('uses the physical receipt of the verified HTTP stream for the live session and terminal summary', async () => {
    const { agent, fetch, session } = createFixtureAgent(parallelEvents)
    try {
      const observed: RunActivity[] = []
      const off = session.subscribe(() => {
        const latest = session.getSnapshot().nativeActivities.at(-1)
        if (latest) observed.push(latest)
      })
      agent.messages = [{ role: 'user', id: 'user-1', content: 'Compare the reports.' }]
      await agent.runAgent({ runId: 'fa2a1653-3b15-4992-bd06-cddec5a27343' })
      off()
      const state = session.getSnapshot()
      expect(state.lastTerminalPhysicalRunId).toBe(physicalRunId)
      expect(observed.some((item) => item.kind === 'responding' && item.status === 'running')).toBe(
        true,
      )
      expect(buildLiveRunSummary(state.nativeActivities)).toMatchObject({
        runId: physicalRunId,
        rootToolCalls: 2,
        rootSubagentCalls: 2,
        status: 'completed',
        isLive: false,
      })
      expect(fetch).toHaveBeenCalledTimes(1)
    } finally {
      agent.dispose()
      session.dispose()
      vi.restoreAllMocks()
    }
  })

  it('observes only lifecycle changes, counts separately scoped sibling work, and closes through the terminal outcome', () => {
    const running = apply([
      textStart,
      tool('delegate', 'dispatch'),
      child('A'),
      child('B'),
      child('nested', 'A'),
      tool('lookup', 'call', 'A'),
      tool('lookup', 'call', 'B'),
      { type: 'TOOL_CALL_END', toolCallId: 'call', subagentRunId: 'B' },
    ])
    expect(buildLiveRunSummary(running, base + 40)).toMatchObject({
      status: 'running',
      isLive: true,
      elapsedMs: 40,
      rootToolCalls: 1,
      descendantToolCalls: 2,
      rootSubagentCalls: 2,
      descendantSubagentCalls: 1,
    })
    expect(
      apply(
        [
          { type: 'TEXT_MESSAGE_CONTENT', messageId: 'answer', delta: 'secret body' },
          { type: 'TOOL_CALL_ARGS', toolCallId: 'call', subagentRunId: 'A', delta: '"secret arg"' },
          { type: 'REASONING_MESSAGE_CONTENT', messageId: 'reason', delta: 'private thought' },
        ],
        running,
      ),
    ).toBe(running)
    const latest = apply(
      [
        result('call', 'B'),
        { type: 'SUBAGENT_FINISHED', subagentRunId: 'B', result: 'private result' },
      ],
      running,
    )
    expect(
      latest
        .filter((item) => item.kind === 'tool' && item.toolCallId === 'call')
        .map(({ namespace, status }) => [namespace, status]),
    ).toEqual([
      [['A'], 'running'],
      [['B'], 'complete'],
    ])
    const done = apply(
      [
        result('call', 'A'),
        result('dispatch'),
        { type: 'TEXT_MESSAGE_END', messageId: 'answer' },
        {
          type: 'RUN_FINISHED',
          threadId,
          runId,
          outcome: { type: 'success' },
          timestamp: base + 100,
        },
      ],
      latest,
    )
    expect(buildLiveRunSummary(done, base + 1_000)).toMatchObject({
      status: 'completed',
      isLive: false,
      elapsedMs: 100,
    })
    expect(JSON.stringify(done)).not.toMatch(/secret|private/)
    expect(apply([{ type: 'RUN_FINISHED', threadId, runId }], done)).toBe(done)
  })

  it('retains pending approval IDs only, isolates declined and failed children, and closes custom indicators', () => {
    const running = apply([
      child('A'),
      child('B'),
      tool('approval', 'approval-B', 'B'),
      { type: 'SUBAGENT_ERROR', subagentRunId: 'A', message: 'private error', code: 'CANCELLED' },
      {
        type: 'SUBAGENT_FINISHED',
        subagentRunId: 'B',
        outcome: { type: 'suspended', interruptIds: ['slot'] },
      },
      {
        type: 'RUN_FINISHED',
        threadId,
        runId,
        outcome: {
          type: 'interrupt',
          interrupts: [
            {
              id: 'slot',
              reason: 'human_input',
              subagentRunId: 'B',
              message: 'private request',
              toolCallId: 'approval-B',
            },
          ],
        },
      },
    ])
    expect(
      running.find((item) => item.title === 'researcher' && item.status === 'cancelled'),
    ).toBeDefined()
    expect(
      running.find((item) => item.kind === 'interrupt' && item.namespace[0] === 'B'),
    ).toMatchObject({
      status: 'requires_action',
      toolCallId: 'approval-B',
    })
    expect(JSON.stringify(running)).not.toContain('private')
    const custom: RunActivity = {
      id: 'compact',
      runId: run,
      namespace: [],
      kind: 'compaction',
      title: 'compaction',
      status: 'running',
    }
    const merged = mergeAgUiRunActivities(
      running,
      [custom, { ...custom, id: 'prior', runId: 'other' }],
      run,
    )
    expect(merged.some((item) => item.id === 'prior')).toBe(false)
    expect(merged.find((item) => item.id === 'compact')?.status).toBe('requires_action')
    expect(buildLiveRunSummary(merged)).toMatchObject({ status: 'interrupted', isLive: false })
  })

  it('uses only the canonical tool failure boolean, uniquely joins an executor result, and distinguishes run cancel and failure', () => {
    const tools = apply([
      child('A'),
      tool('read', 'id'),
      { ...result('id', 'A'), metadata: { moldy: { isError: true, other: 'sensitive' } } },
    ])
    expect(tools.find((item) => item.kind === 'tool')?.status).toBe('error')
    expect(JSON.stringify(tools)).not.toContain('sensitive')
    expect(
      buildLiveRunSummary(
        apply([{ type: 'RUN_FINISHED', threadId, runId, outcome: { type: 'cancelled' } }], tools),
      )?.status,
    ).toBe('canceled')
    expect(
      buildLiveRunSummary(apply([{ type: 'RUN_ERROR', message: 'private error' }], tools))?.status,
    ).toBe('failed')
  })
})
