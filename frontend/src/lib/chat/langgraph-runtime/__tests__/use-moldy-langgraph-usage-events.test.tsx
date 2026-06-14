import { AIMessage } from '@langchain/core/messages'
import { createStore } from 'jotai'
import { beforeEach, describe, expect, it } from 'vitest'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import {
  lastConverterOptions,
  lastRuntimeOptions,
  messageFinishEvent,
  messageStartEvent,
  mocks,
  renderUsageHook,
  resetUsageMocks,
  usageEvent,
} from './use-moldy-langgraph-usage-test-harness'

describe('useMoldyLangGraphStream v3 usage events', () => {
  beforeEach(resetUsageMocks)

  it('hydrates usage from v3 message-finish events', () => {
    const assistantMessage = new AIMessage({ id: 'assistant-usage-4', content: 'done' })
    mocks.stream.messages = [assistantMessage]
    mocks.stream.values = { messages: [assistantMessage] }
    mocks.useChannel.mockImplementation((_stream, channels: readonly string[]) =>
      channels.includes('messages') ? [messageStartEvent(), messageFinishEvent()] : [],
    )

    const store = createStore()
    renderUsageHook(store)

    expect(lastConverterOptions()?.messages[0]?.additional_kwargs?.metadata?.usage).toEqual({
      prompt_tokens: 18,
      completion_tokens: 7,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
    })
    expect(store.get(sessionTokenUsageAtom)).toEqual({
      inputTokens: 18,
      outputTokens: 7,
      cost: 0,
    })
  })

  it('moves run-scoped usage events to the assistant message after message-start mapping arrives', () => {
    const assistantMessage = new AIMessage({ id: 'assistant-usage-4', content: 'done' })
    mocks.stream.messages = [assistantMessage]
    mocks.stream.values = {
      messages: [
        new AIMessage({
          id: 'assistant-usage-4',
          content: 'done',
          usage_metadata: { input_tokens: 18, output_tokens: 7, total_tokens: 25 },
        }),
      ],
    }
    const runScopedUsage = {
      ...usageEvent(''),
      run_id: 'run-message-usage',
      params: {
        namespace: [],
        data: {
          name: 'usage',
          payload: {
            run_id: 'run-message-usage',
            prompt_tokens: 12,
            completion_tokens: 5,
            cache_creation_tokens: 2,
            cache_read_tokens: 3,
            estimated_cost: 0.22,
          },
        },
      },
    }
    mocks.useChannel.mockImplementation((_stream, channels: readonly string[]) => {
      if (channels.includes('custom:usage')) return [runScopedUsage, messageStartEvent()]
      return []
    })

    const store = createStore()
    renderUsageHook(store)

    expect(lastConverterOptions()?.messages[0]?.additional_kwargs?.metadata?.usage).toEqual({
      prompt_tokens: 12,
      completion_tokens: 5,
      cache_creation_tokens: 2,
      cache_read_tokens: 3,
      estimated_cost: 0.22,
    })
    expect(store.get(sessionTokenUsageAtom)).toEqual({
      inputTokens: 12,
      outputTokens: 5,
      cost: 0.22,
    })
  })

  it('keeps converted message identity stable when equivalent stream state replays', () => {
    let conversionCount = 0
    mocks.useExternalMessageConverter.mockImplementation((options) => {
      conversionCount += 1
      return [
        {
          id: 'converted',
          role: 'assistant',
          content: [
            {
              type: 'tool-call',
              toolCallId: 'call-subagent',
              toolName: 'task',
              messages: [
                {
                  id: 'nested-subagent-message',
                  role: 'assistant',
                  content: [],
                  createdAt: new Date(conversionCount * 1000),
                },
              ],
            },
          ],
          metadata: {
            custom: options.messages[0]?.additional_kwargs?.metadata ?? {},
          },
        },
      ]
    })
    const streamedMessage = new AIMessage({ id: 'assistant-stable', content: 'done' })
    const stateMessage = new AIMessage({
      id: 'assistant-stable',
      content: 'done',
      usage_metadata: { input_tokens: 15, output_tokens: 9, total_tokens: 24 },
    })
    mocks.stream.messages = [streamedMessage]
    mocks.stream.values = { messages: [stateMessage] }

    const { rerender } = renderUsageHook()
    const firstMessages = lastConverterOptions()?.messages
    const firstRuntimeMessages = lastRuntimeOptions()?.messages

    mocks.stream.messages = [new AIMessage({ id: 'assistant-stable', content: 'done' })]
    mocks.stream.values = {
      messages: [
        new AIMessage({
          id: 'assistant-stable',
          content: 'done',
          usage_metadata: { input_tokens: 15, output_tokens: 9, total_tokens: 24 },
        }),
      ],
    }
    rerender()

    const secondMessages = lastConverterOptions()?.messages
    const secondRuntimeMessages = lastRuntimeOptions()?.messages
    expect(secondMessages).toBe(firstMessages)
    expect(secondMessages?.[0]).toBe(firstMessages?.[0])
    expect(secondRuntimeMessages).toBe(firstRuntimeMessages)
    expect(secondRuntimeMessages?.[0]).toBe(firstRuntimeMessages?.[0])
  })

  it('refreshes converted messages when the source LangChain message changes', () => {
    mocks.useExternalMessageConverter.mockImplementation((options) => [
      {
        id: 'converted',
        role: 'assistant',
        content: [{ type: 'text', text: String(options.messages[0]?.content ?? '') }],
      },
    ])
    mocks.stream.messages = [new AIMessage({ id: 'assistant-final', content: 'waiting' })]
    mocks.stream.values = { messages: mocks.stream.messages }

    const { rerender } = renderUsageHook()
    const firstRuntimeMessages = lastRuntimeOptions()?.messages

    mocks.stream.messages = [new AIMessage({ id: 'assistant-final', content: 'done' })]
    mocks.stream.values = { messages: mocks.stream.messages }
    rerender()

    const secondRuntimeMessages = lastRuntimeOptions()?.messages
    expect(secondRuntimeMessages).not.toBe(firstRuntimeMessages)
    expect(secondRuntimeMessages?.[0]).not.toBe(firstRuntimeMessages?.[0])
  })
})
