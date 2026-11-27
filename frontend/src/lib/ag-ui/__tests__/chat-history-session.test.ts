import { EventType } from '@ag-ui/core'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api/errors'
import { createAgUiChatSession } from '../chat-history'
import { parseWireChatSnapshot, type WireChatSnapshot } from '../chat-history-types'

const threadId = '9a28c114-5ba9-4208-b511-a191e59ee1f4'
const physical = '3d32c52f-8020-4e32-a0ba-dd86e469a46a'

function snapshot(fields: Record<string, unknown> = {}) {
  return parseWireChatSnapshot(
    {
      threadId,
      protocolVersion: '1.0',
      projectionVersion: '1',
      checkpointId: null,
      activeTipMessageId: null,
      activeRunId: null,
      physicalRunId: null,
      interruptRunId: null,
      physicalInterruptRunId: null,
      cursor: null,
      messages: [],
      messageLinks: [],
      interrupts: [],
      subagents: [],
      ...fields,
    },
    threadId,
  )
}

function sessionFor(readSnapshot: () => Promise<WireChatSnapshot>) {
  return createAgUiChatSession({
    conversationId: threadId,
    readSnapshot,
    getServerMessages: () => [],
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function activity(messageId: string) {
  return {
    type: EventType.ACTIVITY_SNAPSHOT,
    activityType: 'moldy.compaction',
    messageId,
    content: { state: 'running' },
  }
}

describe('AG-UI history session', () => {
  it('keeps a late tool card on the answer that issued its public call ID', () => {
    const session = sessionFor(async () => snapshot())
    const context = { source: 'run' as const, runId: 'accepted' }
    const publicCallId = 'tool-' + 'a'.repeat(64)
    session.onRunAccepted({ runId: context.runId, physicalRunId: physical })
    session.onPublicEvent(
      {
        type: EventType.TOOL_CALL_START,
        toolCallId: publicCallId,
        toolCallName: 'show_note',
        parentMessageId: 'tool-owner',
      },
      context,
    )
    session.onPublicEvent(
      {
        type: EventType.TEXT_MESSAGE_START,
        role: 'assistant',
        messageId: 'later-answer',
      },
      context,
    )
    function showCard(messageId: string, binding: Record<string, string>) {
      session.onPublicEvent(
        {
          type: EventType.ACTIVITY_SNAPSHOT,
          messageId,
          activityType: 'moldy.ui_data',
          content: {
            type: 'demo_note',
            props: { text: 'A note' },
            tool_call_id: 'private-provider-id',
            ...binding,
          },
        },
        context,
      )
    }
    showCard('raw-only', { tool_call_id: publicCallId })
    showCard('wrong-answer', {
      public_tool_call_id: publicCallId,
      public_message_id: 'later-answer',
    })
    expect(session.getSnapshot().activities).toEqual([])
    showCard('owned', { public_tool_call_id: publicCallId })
    expect(session.getSnapshot().activities).toMatchObject([
      {
        messageId: 'owned',
        parentMessageId: 'tool-owner',
        physicalRunId: physical,
        content: { public_tool_call_id: publicCallId },
      },
    ])
    expect([...session.getSnapshot().liveActivityIds]).toEqual(['owned'])
    session.dispose()
  })

  it('ignores buffered callbacks from a reader displaced by a newly accepted command', () => {
    const session = sessionFor(async () => snapshot())
    const stale = { source: 'attach' as const, runId: 'previous-run' }
    session.onRunAccepted({ runId: stale.runId, physicalRunId: physical })
    session.onRunAccepted({
      runId: 'current-run',
      physicalRunId: '657a1ce4-3491-43f4-b33e-499e62555d2f',
    })
    const current = session.getSnapshot()
    session.onPublicEvent(activity('delayed-activity'), stale)
    session.onPublicEvent({ type: EventType.RUN_ERROR, code: 'AG_UI_READ_DENIED' }, stale)
    session.onPublicEvent({ type: EventType.RUN_ERROR, code: 'AG_UI_SNAPSHOT_REQUIRED' }, stale)
    expect(session.getSnapshot()).toBe(current)
    session.onPublicEvent(activity('current-activity'), { source: 'run', runId: 'current-run' })
    expect(session.getSnapshot().activities.map(({ messageId }) => messageId)).toEqual([
      'current-activity',
    ])
    session.dispose()
  })

  it.each([
    [404, true],
    [503, false],
  ] as const)(
    'classifies an initial snapshot HTTP %d by its domain status',
    async (status, denied) => {
      const failure = new ApiError(status, 'snapshot_unavailable', 'Unable to read history')
      const session = sessionFor(async () => {
        throw failure
      })
      await expect(session.history.load()).rejects.toBe(failure)
      expect(session.getSnapshot().readDenied).toBe(denied)
      expect(session.getSnapshot().snapshot).toBeNull()
      session.dispose()
    },
  )

  it('allows one snapshot reconciliation per run and stops reattaching after a second reset', async () => {
    const session = sessionFor(async () =>
      snapshot({ activeRunId: 'active', physicalRunId: physical }),
    )
    await session.history.load()
    const reset = {
      type: EventType.RUN_ERROR,
      code: 'AG_UI_SNAPSHOT_REQUIRED',
      threadId,
      runId: 'active',
    }
    session.onPublicEvent(reset, { source: 'attach' })
    expect(session.getSnapshot()).toMatchObject({ reconcileSequence: 1, attachBlockedRunId: null })
    await session.refreshRepository()
    session.onPublicEvent(reset, { source: 'attach' })
    expect(session.getSnapshot()).toMatchObject({
      reconcileSequence: 1,
      attachBlockedRunId: 'active',
    })
    session.onRunAccepted({ runId: 'next', physicalRunId: physical })
    expect(session.getSnapshot().attachBlockedRunId).toBeNull()
    session.dispose()
  })

  it('clears the visible conversation immediately on read denial and ignores late events', async () => {
    const session = sessionFor(async () =>
      snapshot({
        messages: [{ role: 'user', id: 'q', content: 'Owned conversation' }],
        messageLinks: [{ messageId: 'q', uiMessageId: 'q' }],
        activeTipMessageId: 'q',
        todos: [{ id: 'todo', content: 'Owned task', status: 'pending' }],
        activeRunId: 'active',
        physicalRunId: physical,
      }),
    )
    await session.history.load()
    session.onPublicEvent(activity('visible'), { source: 'attach' })
    expect(session.getSnapshot().snapshot?.messages).toHaveLength(1)
    expect(session.getSnapshot().activities).toHaveLength(1)
    session.onPublicEvent(
      { type: EventType.RUN_ERROR, code: 'AG_UI_READ_DENIED', threadId, runId: 'active' },
      { source: 'attach' },
    )
    session.onPublicEvent(activity('expired'), { source: 'attach' })
    expect(session.getSnapshot()).toMatchObject({
      readDenied: true,
      snapshot: null,
      activeRunId: null,
      physicalRunId: null,
      activities: [],
      messageLinks: [],
      subagents: [],
      interrupts: [],
      deepAgentsState: { todos: [] },
    })
    session.dispose()
  })

  it('never lets a pre-command history read overwrite a newly accepted run', async () => {
    const old = deferred<WireChatSnapshot>()
    const session = sessionFor(() => old.promise)
    const outOfDate = expect(session.refreshRepository()).rejects.toMatchObject({
      name: 'AbortError',
    })
    session.onRunAccepted({ runId: 'just-accepted', physicalRunId: physical })
    old.resolve(snapshot())
    await outOfDate
    expect(session.getSnapshot()).toMatchObject({
      activeRunId: 'just-accepted',
      physicalRunId: physical,
    })
    session.dispose()
  })

  it('restores only public partial output and distinguishes an empty failed run from new input', async () => {
    const partial = {
      text: 'A partial answer',
      truncated: true,
      tools: [{ name: 'read_forecast', state: 'started', args: { note: 'private-tool-input' } }],
    }
    const failure = { failedPhysicalRunId: physical, errorCode: 'partial_model_stream' }
    const read = vi
      .fn<() => Promise<WireChatSnapshot>>()
      .mockResolvedValueOnce(snapshot({ ...failure, partialOutput: partial }))
      .mockResolvedValueOnce(snapshot({ ...failure, partialOutput: null }))
    const session = sessionFor(read)
    await session.history.load()
    expect(session.getSnapshot().partialOutput).toEqual({
      text: 'A partial answer',
      truncated: true,
      tools: [{ name: 'read_forecast', state: 'started' }],
    })
    expect(session.getSnapshot().failedPhysicalRunId).toBe(physical)
    await session.refreshRepository()
    expect(session.getSnapshot().partialOutput).toBeNull()
    session.onRunAccepted({ runId: 'next', physicalRunId: physical })
    expect(session.getSnapshot().failedPhysicalRunId).toBeNull()
    expect(session.getSnapshot().partialOutput).toBeUndefined()
    session.dispose()
  })

  it('stores public and physical run IDs separately and only clears the finished public run', async () => {
    const session = sessionFor(async () =>
      snapshot({ activeRunId: 'public/123', physicalRunId: physical }),
    )
    await session.history.load()
    expect(session.getSnapshot()).toMatchObject({
      activeRunId: 'public/123',
      physicalRunId: physical,
    })
    session.onPublicEvent(
      {
        type: EventType.RUN_FINISHED,
        threadId,
        runId: 'public/stale',
        outcome: { type: 'success' },
      },
      { source: 'attach' },
    )
    expect(session.getSnapshot().activeRunId).toBe('public/123')
    session.onPublicEvent(
      {
        type: EventType.RUN_FINISHED,
        threadId,
        runId: 'public/123',
        outcome: { type: 'success' },
      },
      { source: 'attach' },
    )
    expect(session.getSnapshot().activeRunId).toBeNull()
    session.dispose()
  })

  it('treats history and attached activities as recovered state and marks only a new run activity live', async () => {
    const session = sessionFor(async () =>
      snapshot({
        messages: [
          {
            id: 'stored',
            role: 'activity',
            activityType: 'moldy.compaction',
            content: { state: 'running' },
          },
        ],
        messageLinks: [
          {
            messageId: 'stored',
            uiMessageId: null,
            runId: 'active',
            physicalRunId: physical,
            parentMessageId: null,
            parentCheckpointId: null,
            checkpointId: null,
          },
        ],
        activeRunId: 'active',
        physicalRunId: physical,
      }),
    )
    await session.history.load()
    expect(session.getSnapshot().liveActivityIds.size).toBe(0)
    session.onPublicEvent(
      { type: EventType.RUN_STARTED, threadId, runId: 'active', protocolVersion: '1.0' },
      { source: 'attach' },
    )
    session.onPublicEvent(activity('replayed'), { source: 'attach' })
    expect(session.getSnapshot().liveActivityIds.size).toBe(0)
    session.onPublicEvent(
      { type: EventType.RUN_FINISHED, threadId, runId: 'active', outcome: { type: 'success' } },
      { source: 'attach' },
    )
    session.onRunAccepted({ runId: 'next', physicalRunId: physical })
    session.onPublicEvent(
      { type: EventType.RUN_STARTED, threadId, runId: 'next', protocolVersion: '1.0' },
      { source: 'run' },
    )
    session.onPublicEvent(activity('new-activity'), { source: 'run' })
    session.onPublicEvent(activity('new-activity'), { source: 'run' })
    expect([...session.getSnapshot().liveActivityIds]).toEqual(['new-activity'])
    session.dispose()
  })

  it('rejects a superseded response even when the transport delivers it after the new branch', async () => {
    const old = deferred<WireChatSnapshot>()
    const current = deferred<WireChatSnapshot>()
    const read = vi
      .fn<() => Promise<WireChatSnapshot>>()
      .mockReturnValueOnce(old.promise)
      .mockReturnValueOnce(current.promise)
    const session = sessionFor(read)
    const first = session.refreshRepository()
    const outdated = expect(first).rejects.toMatchObject({ name: 'AbortError' })
    const second = session.refreshRepository()
    current.resolve(snapshot({ activeRunId: 'new-branch', physicalRunId: physical }))
    await second
    old.resolve(snapshot({ activeRunId: 'old-branch', physicalRunId: physical }))
    await outdated
    expect(session.getSnapshot().activeRunId).toBe('new-branch')
    session.dispose()
  })

  it('never notifies a disposed conversation subscriber when an old GET finally returns', async () => {
    const waiting = deferred<WireChatSnapshot>()
    const session = sessionFor(() => waiting.promise)
    const changed = vi.fn()
    session.subscribe(changed)
    const read = session.refreshRepository()
    const disposed = expect(read).rejects.toMatchObject({ name: 'AbortError' })
    session.dispose()
    changed.mockClear()
    waiting.resolve(snapshot({ activeRunId: 'departed', physicalRunId: physical }))
    await disposed
    expect(changed).not.toHaveBeenCalled()
    expect(session.getSnapshot().activeRunId).toBeNull()
  })
})
