import { AIMessage, HumanMessage, ToolMessage } from '@langchain/core/messages'
import { unstable_convertExternalMessages } from '@assistant-ui/react'
import { describe, expect, it } from 'vitest'
import { dedupeLangChainMessagesById, dedupeThreadMessagesById } from '../message-list'
import { convertMoldyLangChainMessage } from '../langchain-message-conversion'

describe('dedupeLangChainMessagesById', () => {
  it('keeps the latest message when LangGraph reuses a message id', () => {
    const oldMessage = new AIMessage({ id: 'lc-run-1', content: 'old' })
    const newMessage = new AIMessage({ id: 'lc-run-1', content: 'new' })
    const userMessage = new HumanMessage({ id: 'user-1', content: 'hello' })

    const result = dedupeLangChainMessagesById([userMessage, oldMessage, newMessage])

    expect(result).toEqual([userMessage, newMessage])
  })

  it('updates duplicate LangChain messages in their first stable position', () => {
    const firstUser = new HumanMessage({ id: 'user-1', content: 'hello' })
    const oldAssistant = new AIMessage({ id: 'lc-run-1', content: 'old' })
    const toolResult = new HumanMessage({ id: 'user-2', content: 'next' })
    const newAssistant = new AIMessage({ id: 'lc-run-1', content: 'new' })

    const result = dedupeLangChainMessagesById([firstUser, oldAssistant, toolResult, newAssistant])

    expect(result).toEqual([firstUser, newAssistant, toolResult])
    expect(result[1]).toBe(newAssistant)
  })

  it('preserves messages without ids because they cannot collide in assistant-ui keys', () => {
    const first = new AIMessage({ content: 'first' })
    const second = new AIMessage({ content: 'second' })

    const result = dedupeLangChainMessagesById([first, second])

    expect(result).toEqual([first, second])
  })

  it('prevents duplicate assistant-ui thread ids for repeated LangGraph tool-call messages', () => {
    const userMessage = new HumanMessage({ id: 'user-1', content: 'hello' })
    const toolCallMessage = new AIMessage({
      id: 'lc_run--tool',
      content: '',
      tool_calls: [{ id: 'call-tool', name: 'execute_in_skill', args: { command: 'build' } }],
    })
    const duplicateToolCallMessage = new AIMessage({
      id: 'lc_run--tool',
      content: '',
      tool_calls: [{ id: 'call-tool', name: 'execute_in_skill', args: { command: 'build' } }],
    })
    const toolResult = new ToolMessage({
      id: 'tool-result',
      content: 'done',
      name: 'execute_in_skill',
      tool_call_id: 'call-tool',
    })
    const finalMessage = new AIMessage({ id: 'lc_run--final', content: 'complete' })

    const source = dedupeLangChainMessagesById([
      userMessage,
      toolCallMessage,
      duplicateToolCallMessage,
      toolResult,
      finalMessage,
    ])
    const threadMessages = unstable_convertExternalMessages(
      [...source],
      convertMoldyLangChainMessage,
      false,
      {},
    )
    const ids = threadMessages.map((message) => message.id)

    expect(ids).toEqual([...new Set(ids)])
  })
})

describe('dedupeThreadMessagesById', () => {
  it('updates duplicate assistant-ui messages in place with the latest payload', () => {
    const firstUser = { id: 'user-1', role: 'user', content: [{ type: 'text', text: 'hello' }] }
    const oldAssistant = {
      id: 'lc_run--1',
      role: 'assistant',
      content: [{ type: 'text', text: 'old' }],
    }
    const secondUser = { id: 'user-2', role: 'user', content: [{ type: 'text', text: 'next' }] }
    const newAssistant = {
      id: 'lc_run--1',
      role: 'assistant',
      content: [{ type: 'text', text: 'new' }],
    }

    const result = dedupeThreadMessagesById([firstUser, oldAssistant, secondUser, newAssistant])

    expect(result).toEqual([firstUser, newAssistant, secondUser])
    expect(result[1]).toBe(newAssistant)
  })

  it('keeps array identity when assistant-ui message ids are already unique', () => {
    const messages = [
      { id: 'user-1', role: 'user' },
      { id: 'assistant-1', role: 'assistant' },
    ]

    const result = dedupeThreadMessagesById(messages)

    expect(result).toBe(messages)
  })
})
