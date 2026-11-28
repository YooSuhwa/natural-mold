import { describe, expect, it } from 'vitest'
import {
  parsePublicActivities,
  parsePublicInterrupts,
  parseWireChatSnapshot,
} from '../chat-history-types'

const threadId = 'conversation'
const runId = 'public/run'
const physicalRunId = 'physical-run'
const envelope = {
  threadId,
  protocolVersion: '1.0',
  projectionVersion: '1',
  messages: [],
  messageLinks: [],
  subagents: [],
  interrupts: [],
  todos: [],
}

describe('public chat snapshot decoding', () => {
  it('retains the selected physical run when a cancelled run has no messages', () => {
    const selected = {
      runId: 'historical/public',
      runStatus: 'cancelled',
      selectedPhysicalRunId: 'historical-db',
      latestPhysicalRunId: 'current-db',
      activeRunId: 'current/public',
      physicalRunId: 'current-db',
    }
    const snapshot = parseWireChatSnapshot({ ...envelope, ...selected }, threadId)
    expect(snapshot).toMatchObject(selected)
    expect(snapshot.messages).toEqual([])
    expect(parseWireChatSnapshot(envelope, threadId).selectedPhysicalRunId).toBeNull()
  })

  it('restores the selected queue receipt and drops private request fields', () => {
    const receipt = {
      runId,
      inputId: 'input',
      inputStatus: 'pending',
      inputRevision: 2,
      queueStatus: 'pending',
      position: 3,
    }
    const recovered = parseWireChatSnapshot(
      {
        ...envelope,
        ...receipt,
        client_request_id: 'private-request',
      },
      threadId,
    )
    expect(recovered).toMatchObject(receipt)
    expect(recovered).not.toHaveProperty('client_request_id')
    expect(recovered.activeRunId).toBeNull()
    expect(parseWireChatSnapshot(envelope, threadId)).toMatchObject({
      inputId: null,
      inputStatus: null,
      inputRevision: null,
      queueStatus: null,
      position: null,
    })
  })

  it('restores native activity messages through their own message links and strips protected file data', () => {
    const row = {
      role: 'activity',
      id: 'file',
      activityType: 'moldy.file_event',
      content: {
        id: 'artifact',
        version_id: 'version',
        op: 'updated',
        display_name: 'private-name',
      },
    }
    const snapshot = parseWireChatSnapshot(
      {
        ...envelope,
        messages: [row, { ...row, id: 'unbound' }],
        messageLinks: [
          { messageId: 'file', uiMessageId: null, runId, physicalRunId, parentMessageId: 'answer' },
        ],
        todos: [
          { id: 'todo', content: 'Review result', status: 'completed', notes: 'private-notes' },
        ],
      },
      threadId,
    )
    expect(snapshot.messages).toEqual([])
    expect(snapshot.activities).toHaveLength(1)
    expect(snapshot.activities[0]).toMatchObject({
      messageId: 'file',
      physicalRunId,
      parentMessageId: 'answer',
      content: { id: 'artifact', version_id: 'version', op: 'updated' },
    })
    expect(snapshot.state.todos[0]).toEqual({
      id: 'todo',
      content: 'Review result',
      status: 'completed',
    })
    expect(JSON.stringify(snapshot)).not.toMatch(/private-name|private-notes/)
  })

  it('keeps the protected and public child tool IDs without materializing a private widget', () => {
    const [pointer] = parsePublicActivities([
      {
        type: 'ACTIVITY_SNAPSHOT',
        activityType: 'moldy.mcp_app',
        messageId: 'widget',
        subagentRunId: 'child',
        content: {
          run_id: 'r',
          binding_id: 'b',
          tool_call_id: 'call',
          public_tool_call_id: 'child::call',
          result_meta: { html: 'hidden' },
        },
      },
    ])
    expect(pointer.content).toEqual({
      run_id: 'r',
      binding_id: 'b',
      tool_call_id: 'call',
      public_tool_call_id: 'child::call',
    })
    expect(
      parsePublicActivities([
        {
          type: 'ACTIVITY_SNAPSHOT',
          activityType: 'moldy.memory_recalled',
          messageId: 'recall',
          content: { memories: [{ id: 'm', scope: 'agent', content: 'private-memory' }] },
        },
      ])[0].content.memories,
    ).toEqual([{ id: 'm', scope: 'agent', content: '<redacted>' }])
  })

  it('preserves the distinct public tool ID for a scoped UI card and drops incomplete bindings', () => {
    const content = { type: 'demo_note', props: { text: 'Done' }, tool_call_id: 'provider-call' }
    const ui = { type: 'ACTIVITY_SNAPSHOT', messageId: 'card', activityType: 'moldy.ui_data' }
    const parsed = parsePublicActivities([
      { ...ui, content: { ...content, public_tool_call_id: 'wire-call' } },
      { ...ui, content },
      { ...ui, activityType: 'moldy.mcp_app', content },
    ])
    expect(parsed).toHaveLength(1)
    expect(parsed[0].content).toMatchObject({
      tool_call_id: 'provider-call',
      public_tool_call_id: 'wire-call',
    })
  })

  it('keeps the latest auth attempt for each owner and rejects an ambiguous resumable ID', () => {
    function slot(id: string, owner: string, attempt: number) {
      return {
        id,
        reason: 'moldy.auth_required',
        subagentRunId: owner,
        metadata: {
          moldy: {
            type: 'auth_required',
            auth_request_id: 'same',
            reason: 'expired',
            attempt,
            items: [
              {
                resource_name: 'calendar',
                fixable_by: 'owner',
                credential_id: 'private-credential',
              },
            ],
          },
        },
      }
    }
    const first = slot('old', 'child-a', 1)
    const second = slot('current', 'child-a', 2)
    const other = slot('other', 'child-b', 1)
    const current = parsePublicInterrupts([second, other, first])
    expect(current.map(({ id }) => id)).toEqual(['current', 'other'])
    expect(JSON.stringify(current)).not.toContain('private-credential')
    expect(() => parsePublicInterrupts([second, { ...other, id: second.id }])).toThrow()
  })

  it('retains read-only slots even when only the visible prompt survives', () => {
    const entries = [
      {
        id: 'visible',
        reason: 'moldy.ask_user',
        message: 'Continue?',
        metadata: { moldy: { type: 'ask_user', question: 'Continue?', actionable: false } },
      },
      {
        id: 'limited',
        reason: 'moldy.auth_required',
        subagentRunId: 's1',
        message: 'Waiting',
        responseSchema: { type: 'object' },
        metadata: { moldy: { actionable: false, internal: 'private' } },
      },
      { id: 'normal', reason: 'moldy.input_required', metadata: { moldy: { actionable: true } } },
    ]
    const [visible, limited, normal] = parsePublicInterrupts(entries)
    expect(visible.metadata?.moldy).toMatchObject({ type: 'ask_user', actionable: false })
    expect(limited.metadata?.moldy).toEqual({ actionable: false })
    expect(limited.responseSchema).toBeUndefined()
    expect(normal.metadata).toBeUndefined()
  })

  it('honors a legacy child grant only for its current slot and run', () => {
    const actor = {
      subagentRunId: 'child',
      runId,
      physicalRunId,
      parentToolCallId: 'call',
      name: 'Worker',
      status: 'interrupted',
      interruptIds: ['current'],
      legacyInterruptAccess: true,
    }
    const details = { type: 'ask_user', question: 'Continue?', legacyInterruptAccess: true }
    const slot = {
      id: 'current',
      subagentRunId: 'child',
      reason: 'moldy.ask_user',
      metadata: { moldy: details },
    }
    const current = {
      ...envelope,
      interruptRunId: runId,
      physicalInterruptRunId: physicalRunId,
      subagents: [actor],
      interrupts: [slot],
    }
    const allowed = parseWireChatSnapshot(current, threadId)
    expect(allowed.subagents[0].legacyInterruptAccess).toBe(true)
    expect(allowed.interrupts[0].metadata?.moldy?.legacyInterruptAccess).toBe(true)
    const denied = [
      { ...current, interruptRunId: null },
      { ...current, physicalInterruptRunId: 'another-run' },
      { ...current, subagents: [{ ...actor, legacyInterruptAccess: false }] },
      { ...current, subagents: [{ ...actor, interruptIds: ['expired'] }] },
      { ...current, interrupts: [{ ...slot, subagentRunId: 'sibling' }] },
      {
        ...current,
        interrupts: [{ ...slot, metadata: { moldy: { ...details, actionable: false } } }],
      },
    ]
    for (const snapshot of denied) {
      const parsed = parseWireChatSnapshot(snapshot, threadId)
      expect(parsed.interrupts[0].metadata?.moldy?.legacyInterruptAccess).toBeUndefined()
      expect(parsed.subagents[0].legacyInterruptAccess).toBeUndefined()
    }
    expect(parsePublicInterrupts([slot])[0].metadata?.moldy?.legacyInterruptAccess).toBeUndefined()
  })

  it('decodes only a partial response scoped to a confirmed failed run', () => {
    const partialOutput = {
      text: 'partial',
      tools: [{ name: 'search', state: 'unknown', args: 'private-args' }],
      truncated: false,
    }
    const failure = { failedPhysicalRunId: physicalRunId, errorCode: 'partial_model_stream' }
    expect(
      parseWireChatSnapshot({ ...envelope, ...failure, partialOutput }, threadId).partialOutput,
    ).toEqual({ text: 'partial', tools: [{ name: 'search', state: 'unknown' }], truncated: false })
    expect(
      parseWireChatSnapshot({ ...envelope, partialOutput }, threadId).partialOutput,
    ).toBeUndefined()
    expect(parseWireChatSnapshot(envelope, threadId).partialOutput).toBeUndefined()
  })
})
