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
