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
  it('keeps original tool identities when REST approval aliases reconcile with checkpoint hydration', () => {
    const converted = messagesFromServerMessages([
      serverMessage({
        id: 'public-assistant-tool',
        runtime_message_id: 'checkpoint-assistant-tool',
        tool_calls: [
          {
            id: 'call-1',
            name: 'request_approval',
            args: {
              tool_name: 'execute_in_skill',
              tool_args: { command: 'make-docx' },
              hitl_interrupt_id: 'interrupt-1',
            },
          },
        ],
      }),
    ])
    const checkpoint = new AIMessage({
      id: 'checkpoint-assistant-tool',
      content: '',
      tool_calls: [{ id: 'call-1', name: 'execute_in_skill', args: { command: 'make-docx' } }],
    })
    expect(converted[0].id).toBe(checkpoint.id)
    expect(AIMessage.isInstance(converted[0]) ? converted[0].tool_calls : null).toEqual(
      checkpoint.tool_calls,
    )
  })

  it.each([{}, { tool_name: 'execute_in_skill', tool_args: [] }])(
    'does not import a malformed approval alias as a runtime tool declaration (%j)',
    (args) => {
      const converted = messagesFromServerMessages([
        serverMessage({ tool_calls: [{ id: 'call-1', name: 'request_approval', args }] }),
      ])
      expect(AIMessage.isInstance(converted[0]) ? converted[0].tool_calls : null).toEqual([])
    },
  )

  it('does not attach a non-empty tool_calls array for plain assistant text turns', () => {
    const converted = messagesFromServerMessages([
      serverMessage({ id: 'assistant-text', role: 'assistant', content: '안녕하세요' }),
    ])

    const message = converted[0]
    expect(AIMessage.isInstance(message) ? message.tool_calls : null).toEqual([])
  })
})
