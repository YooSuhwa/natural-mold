import { AIMessage, HumanMessage, isHumanMessage } from '@langchain/core/messages'
import { describe, expect, it } from 'vitest'
import type { Message as MoldyMessage } from '@/lib/types'
import {
  appendPendingNewSubmitMessage,
  messagesFromServerMessages,
} from '../use-moldy-langgraph-stream'

type PendingNewSubmit = Parameters<typeof appendPendingNewSubmitMessage>[1]

function pendingSubmit(content: string, baseMessageCount: number): PendingNewSubmit {
  return {
    conversationId: 'conv-1',
    content,
    baseMessageCount,
    message: new HumanMessage({ id: `pending:${content}`, content }),
  }
}

function serverMessage(overrides: Partial<MoldyMessage>): MoldyMessage {
  return {
    id: 'm-1',
    conversation_id: 'conv-1',
    role: 'assistant',
    content: '',
    tool_calls: null,
    tool_call_id: null,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

describe('appendPendingNewSubmitMessage', () => {
  it('inserts the optimistic human bubble at the captured base index in the normal case', () => {
    const messages = [
      new HumanMessage({ id: 'u-1', content: '첫 질문' }),
      new AIMessage({ id: 'a-1', content: '답변' }),
    ]

    const result = appendPendingNewSubmitMessage(messages, pendingSubmit('두번째 질문', 2))

    expect(result).toHaveLength(3)
    expect(isHumanMessage(result[2]) ? result[2].content : null).toBe('두번째 질문')
  })

  it('clamps the insertion to the tail when the raw list shrinks below baseMessageCount', () => {
    // baseMessageCount captured at 4, but the list shrank to 2 messages between
    // capture and render. The captured index must not insert mid-list.
    const messages = [
      new HumanMessage({ id: 'u-1', content: '첫 질문' }),
      new AIMessage({ id: 'a-1', content: '답변' }),
    ]

    const result = appendPendingNewSubmitMessage(messages, pendingSubmit('두번째 질문', 4))

    expect(result).toHaveLength(3)
    // The optimistic bubble lands at the tail, never mid-list.
    expect(isHumanMessage(result[2]) ? result[2].content : null).toBe('두번째 질문')
    expect(result.slice(0, 2)).toEqual(messages)
  })

  it('does not insert when the pending message is already visible', () => {
    const messages = [new HumanMessage({ id: 'u-1', content: '이미 보임' })]

    const result = appendPendingNewSubmitMessage(messages, pendingSubmit('이미 보임', 0))

    expect(result).toBe(messages)
  })
})

describe('messagesFromServerMessages', () => {
  it('preserves assistant tool_calls so tool-call-only turns are not blank', () => {
    const converted = messagesFromServerMessages([
      serverMessage({
        id: 'assistant-tool',
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'call-1', name: 'web_search', args: { query: 'moldy' } }],
      }),
    ])

    expect(converted).toHaveLength(1)
    const message = converted[0]
    expect(AIMessage.isInstance(message)).toBe(true)
    expect(AIMessage.isInstance(message) ? message.tool_calls : []).toEqual([
      { id: 'call-1', name: 'web_search', args: { query: 'moldy' } },
    ])
  })

  it('does not attach an empty tool_calls array for plain assistant text turns', () => {
    const converted = messagesFromServerMessages([
      serverMessage({ id: 'assistant-text', role: 'assistant', content: '안녕하세요' }),
    ])

    const message = converted[0]
    expect(AIMessage.isInstance(message) ? message.tool_calls : null).toEqual([])
  })
})
