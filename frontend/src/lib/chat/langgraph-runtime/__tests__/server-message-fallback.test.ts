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
  // The degraded server-message fallback intentionally does NOT carry assistant
  // tool_calls. Reconstructing them here conflicts with the primary checkpointer
  // hydration on reload: assistant-ui reconciles the two representations of the
  // same message (by id) and throws "Tool call name … does not match existing
  // tool call …", which breaks the HITL approval card after reload (found by the
  // chat-langgraph-v3 E2E specs). A tool-call-only turn rendering momentarily
  // blank in the rare degraded window is far less harmful than breaking reload.
  it('does not carry assistant tool_calls (avoids reload reconciliation conflict)', () => {
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
    expect(AIMessage.isInstance(message) ? message.tool_calls : null).toEqual([])
  })

  it('does not attach a non-empty tool_calls array for plain assistant text turns', () => {
    const converted = messagesFromServerMessages([
      serverMessage({ id: 'assistant-text', role: 'assistant', content: '안녕하세요' }),
    ])

    const message = converted[0]
    expect(AIMessage.isInstance(message) ? message.tool_calls : null).toEqual([])
  })
})
