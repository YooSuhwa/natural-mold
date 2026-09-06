import { describe, expect, it } from 'vitest'
import {
  interruptsFromThreadState,
  messageMetadataFromThreadState,
  messagesFromServerMessages,
  messagesFromThreadState,
  terminalRunNoticeFromThreadState,
} from '../stream-thread-state-projection'

describe('stream thread state projection', () => {
  it('projects messages, metadata, interrupts, and terminal state from one snapshot', () => {
    const state = {
      metadata: { latest_run: { id: 'run-1', status: 'failed', error_message: 'failed' } },
      values: {
        messages: [
          {
            type: 'human',
            id: 'message-1',
            content: 'hello',
            additional_kwargs: { metadata: { branchIndex: 1 } },
          },
        ],
        __interrupt__: [{ id: 'interrupt-values', value: {} }],
      },
      tasks: [{ interrupts: [{ id: 'interrupt-task', value: {} }] }],
    }

    expect(messagesFromThreadState(state)?.map((message) => message.id)).toEqual(['message-1'])
    expect(messageMetadataFromThreadState(state).byId.get('message-1')).toEqual({ branchIndex: 1 })
    expect(interruptsFromThreadState(state).map((interrupt) => interrupt.id)).toEqual([
      'interrupt-values',
      'interrupt-task',
    ])
    expect(terminalRunNoticeFromThreadState(state)).toEqual({
      id: 'run-1',
      status: 'failed',
      errorMessage: 'failed',
    })
  })

  it('returns bounded empty projections for malformed snapshot fields', () => {
    const state = {
      metadata: { latest_run: { id: 42, status: 'failed' } },
      values: { messages: [null, { content: 'missing role' }], __interrupt__: 'invalid' },
      tasks: [{ interrupts: 'invalid' }],
    }

    expect(messagesFromThreadState(state)).toEqual([])
    expect(messageMetadataFromThreadState(state).byId.size).toBe(0)
    expect(interruptsFromThreadState(state)).toEqual([])
    expect(terminalRunNoticeFromThreadState(state)).toBeNull()
  })

  it('preserves public server message role conversion', () => {
    const messages = messagesFromServerMessages([
      {
        id: 'user-1',
        conversation_id: 'conversation-1',
        role: 'user',
        content: 'question',
        tool_calls: null,
        tool_call_id: null,
        created_at: '2026-09-01T00:00:00Z',
      },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: 'answer',
        conversation_id: 'conversation-1',
        tool_calls: null,
        tool_call_id: null,
        created_at: '2026-09-01T00:00:01Z',
      },
      {
        id: 'tool-1',
        role: 'tool',
        content: 'result',
        conversation_id: 'conversation-1',
        tool_calls: null,
        tool_call_id: 'call-1',
        created_at: '2026-09-01T00:00:02Z',
      },
    ])

    expect(messages.map((message) => message._getType())).toEqual(['human', 'ai', 'tool'])
    expect(messages.map((message) => message.id)).toEqual(['user-1', 'assistant-1', 'tool-1'])
  })
})
