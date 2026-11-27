import { describe, expect, it } from 'vitest'
import { restoreAgUiNativeActivities } from '../activity-native-restore'
import { parseWireChatSnapshot } from '../chat-history-types'

const thread = 'public-thread'
const physical = 'physical-run'
const publicRun = 'public-run'
function snapshot(overrides: Record<string, unknown> = {}) {
  return parseWireChatSnapshot(
    {
      threadId: thread,
      protocolVersion: '1.0',
      projectionVersion: '1',
      runId: publicRun,
      runStatus: 'running',
      activeRunId: publicRun,
      physicalRunId: physical,
      messages: [],
      messageLinks: [],
      interrupts: [],
      subagents: [],
      ...overrides,
    },
    thread,
  )
}
function linked(
  messageId: string,
  subagentRunId?: string,
  parentMessageId?: string,
  runId = physical,
) {
  return { messageId, runId: publicRun, physicalRunId: runId, subagentRunId, parentMessageId }
}
function assistant(id: string, toolId: string, lane?: string) {
  return {
    role: 'assistant',
    id,
    subagentRunId: lane,
    content: 'private response',
    toolCalls: [
      { type: 'function', id: toolId, function: { name: 'lookup', arguments: '{"secret":true}' } },
    ],
  }
}
function child(id: string, status: string, parent?: string) {
  return {
    subagentRunId: id,
    name: 'Researcher',
    status,
    parentSubagentRunId: parent,
    runId: publicRun,
    physicalRunId: physical,
    result: 'private child output',
  }
}

describe('persisted AG-UI activity summary', () => {
  it('keeps the same SDK nesting bound for either snapshot order', () => {
    const chain = Array.from({ length: 20 }, (_, i) =>
      child('child-' + i, 'running', i ? 'child-' + (i - 1) : undefined),
    )
    for (const subagents of [chain, [...chain].reverse()]) {
      const rows = restoreAgUiNativeActivities(snapshot({ subagents }))
      expect(rows).toHaveLength(16)
      expect(rows.at(-1)?.namespace).toHaveLength(15)
    }
  })

  it('uses the physical run, verified child lanes and issuing message without carrying transcripts', () => {
    const source = snapshot({
      messages: [
        assistant('root', 'search-root'),
        assistant('B', 'search-B', 'second'),
        {
          role: 'tool',
          id: 'answer-B',
          toolCallId: 'search-root',
          subagentRunId: 'second',
          content: 'private result',
        },
        assistant('old', 'old-call'),
        assistant('foreign', 'wrong-owner', 'unknown'),
      ],
      messageLinks: [
        linked('root'),
        linked('B', 'second'),
        linked('answer-B', 'second', 'root'),
        linked('old', undefined, undefined, 'different-run'),
        linked('foreign', 'unknown'),
      ],
      subagents: [
        child('second', 'running', 'first'),
        child('first', 'completed'),
        { ...child('unknown', 'running'), physicalRunId: 'different-run' },
      ],
    })
    const activities = restoreAgUiNativeActivities(source)
    expect(activities.filter((row) => row.kind === 'tool')).toMatchObject([
      { toolCallId: 'search-root', status: 'complete', namespace: [] },
      { toolCallId: 'search-B', status: 'running', namespace: ['first', 'second'] },
    ])
    expect(activities.filter((row) => row.kind === 'subagent')).toMatchObject([
      { title: 'Researcher', status: 'complete', namespace: [] },
      { title: 'Researcher', status: 'running', namespace: ['first'] },
    ])
    expect(activities.every((row) => row.runId === physical && !row.startedAt)).toBe(true)
    expect(activities.some((row) => row.kind === 'responding')).toBe(false)
    expect(JSON.stringify(activities)).not.toMatch(/private|secret|old-call|wrong-owner/)
  })

  it('rejects cyclic children and unrelated results, and keeps failures and pending decisions scoped', () => {
    const fields = {
      activeRunId: null,
      physicalRunId: null,
      latestPhysicalRunId: physical,
      runStatus: 'interrupted',
      physicalInterruptRunId: physical,
      interruptRunId: publicRun,
      messages: [
        assistant('root', 'call'),
        { role: 'tool', id: 'other', toolCallId: 'call', content: 'wrong result' },
      ],
      messageLinks: [linked('root'), linked('other', undefined, 'unrelated')],
      subagents: [
        child('cycle-A', 'completed', 'cycle-B'),
        child('cycle-B', 'completed', 'cycle-A'),
      ],
      interrupts: [
        { id: 'decision', reason: 'approval', toolCallId: 'call', message: 'private confirmation' },
      ],
    }
    const restored = restoreAgUiNativeActivities(snapshot(fields))
    expect(restored.find((item) => item.kind === 'tool')).toMatchObject({
      status: 'requires_action',
    })
    expect(restored.some((item) => item.kind === 'subagent')).toBe(false)
    expect(restored.some((item) => item.kind === 'interrupt' && item.toolCallId === 'call')).toBe(
      true,
    )
    expect(JSON.stringify(restored)).not.toContain('private')
    const failed = restoreAgUiNativeActivities(
      snapshot({
        ...fields,
        runStatus: 'failed',
        failedPhysicalRunId: physical,
        physicalInterruptRunId: null,
      }),
    )
    expect(failed.at(-1)).toMatchObject({ kind: 'error', status: 'error' })
    expect(failed.some((item) => item.kind === 'interrupt')).toBe(false)
    const stale = restoreAgUiNativeActivities(
      snapshot({ ...fields, runStatus: 'stale', physicalInterruptRunId: null }),
    )
    expect(stale.at(-1)?.terminalRunStatus).toBe('stale')
  })

  it('omits unbound calls and refuses to label the displayed run using another run status', () => {
    const fields = {
      activeRunId: null,
      physicalRunId: null,
      latestPhysicalRunId: physical,
      messages: [assistant('only', 'call'), assistant('ambiguous', 'hidden')],
      messageLinks: [linked('only'), linked('ambiguous'), linked('ambiguous')],
      runId: 'different-public-run',
      runStatus: 'completed',
    }
    const rows = restoreAgUiNativeActivities(snapshot(fields))
    expect(rows.map((item) => item.toolCallId)).toEqual(['call'])
    expect(restoreAgUiNativeActivities(snapshot({ ...fields, latestPhysicalRunId: null }))).toEqual(
      [],
    )
    const finished = restoreAgUiNativeActivities(snapshot({ ...fields, runId: publicRun }))
    expect(finished.some((item) => item.kind === 'responding' && item.status === 'complete')).toBe(
      true,
    )
    expect(finished.at(-1)).toMatchObject({ kind: 'done', status: 'complete' })
  })
})
