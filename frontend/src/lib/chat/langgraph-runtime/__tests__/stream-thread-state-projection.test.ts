import { AIMessage, ToolMessage } from '@langchain/core/messages'
import { describe, expect, it } from 'vitest'
import {
  interruptsFromThreadState,
  messageMetadataFromThreadState,
  completedRunIdFromThreadState,
  messagesFromServerMessages,
  messagesFromThreadState,
  terminalRunNoticeFromThreadState,
} from '../stream-thread-state-projection'

describe('stream thread state projection', () => {
  it('correlates only a completed exact run for replay hydration', () => {
    expect(
      completedRunIdFromThreadState({
        metadata: { latest_run: { id: 'run', status: 'completed' } },
      }),
    ).toBe('run')
    expect(
      completedRunIdFromThreadState({ metadata: { latest_run: { id: 'old', status: 'running' } } }),
    ).toBeNull()
    expect(
      completedRunIdFromThreadState({ metadata: { latest_run: { status: 'completed' } } }),
    ).toBeNull()
    expect(completedRunIdFromThreadState(null)).toBeNull()
  })

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

  it('keeps checkpoint identities in REST fallback before state hydration', () => {
    const fallback = [
      {
        id: '38d03419-01f7-51d9-a33e-cfedcaec0732',
        runtime_message_id: 'lc_run--fallback-answer',
        conversation_id: 'conversation-1',
        role: 'assistant' as const,
        content: 'Pin this answer',
        tool_calls: null,
        tool_call_id: null,
        created_at: '2026-09-01T00:00:00Z',
      },
    ]
    const hydrated = messagesFromThreadState({
      values: {
        messages: [
          {
            type: 'ai',
            id: 'lc_run--fallback-answer',
            content: 'Pin this answer',
          },
        ],
      },
    })
    const displayed = messagesFromServerMessages(fallback)
    expect(displayed[0]?.id).toBe('lc_run--fallback-answer')
    expect(displayed[0]?.additional_kwargs.metadata).toEqual({ publicMessageId: fallback[0].id })
    expect(displayed[0]?.id).toBe(hydrated?.[0]?.id)
    expect(fallback[0]?.id).toBe('38d03419-01f7-51d9-a33e-cfedcaec0732')
  })

  it('retains REST tool declarations and their result correlation during fallback', () => {
    const toolCalls = [
      {
        id: 'interrupt:0',
        name: 'execute_in_skill',
        args: { command: 'make-docx' },
      },
      {
        id: 'interrupt:1',
        name: 'execute_in_skill',
        args: { command: 'make-second-docx' },
      },
    ]
    const envelope = {
      conversation_id: 'conversation-1',
      tool_call_id: null,
      created_at: '2026-09-01T00:00:00Z',
    }
    const messages = messagesFromServerMessages([
      {
        ...envelope,
        id: 'public-approval',
        runtime_message_id: 'runtime-approval',
        role: 'assistant',
        content: '',
        tool_calls: toolCalls,
      },
      {
        ...envelope,
        id: 'public-result',
        runtime_message_id: 'runtime-result',
        role: 'tool',
        content: '{"decision":"approved"}',
        tool_calls: null,
        tool_call_id: 'interrupt:0',
      },
    ])
    expect(AIMessage.isInstance(messages[0])).toBe(true)
    expect((messages[0] as AIMessage).tool_calls).toEqual(toolCalls)
    expect(ToolMessage.isInstance(messages[1])).toBe(true)
    expect((messages[1] as ToolMessage).tool_call_id).toBe('interrupt:0')
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
