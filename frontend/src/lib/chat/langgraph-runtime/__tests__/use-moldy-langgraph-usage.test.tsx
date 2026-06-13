import { AIMessage } from '@langchain/core/messages'
import { createStore } from 'jotai'
import { beforeEach, describe, expect, it } from 'vitest'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import {
  lastConverterOptions,
  mocks,
  renderUsageHook,
  resetUsageMocks,
  usagePayload,
} from './use-moldy-langgraph-usage-test-harness'

describe('useMoldyLangGraphStream usage metadata', () => {
  beforeEach(resetUsageMocks)

  it('attaches dedicated v3 usage payloads to assistant-ui message metadata and session totals', () => {
    const assistantMessage = new AIMessage({ id: 'assistant-usage-1', content: 'done' })
    mocks.stream.messages = [assistantMessage]
    mocks.stream.values = { messages: [assistantMessage] }
    mocks.useChannel.mockImplementation((_stream, channels: readonly string[]) =>
      channels.includes('custom:usage') ? [usagePayload()] : [],
    )

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

  it('hydrates usage from LangGraph message usage metadata without a live custom event', () => {
    const assistantMessage = new AIMessage({
      id: 'assistant-usage-2',
      content: 'done',
      usage_metadata: {
        input_tokens: 8,
        output_tokens: 4,
        total_tokens: 12,
        input_token_details: {
          cache_creation: 1,
          cache_read: 2,
        },
      },
    })
    mocks.stream.messages = [assistantMessage]
    mocks.stream.values = { messages: [assistantMessage] }

    const store = createStore()
    renderUsageHook(store)

    expect(lastConverterOptions()?.messages[0]?.additional_kwargs?.metadata?.usage).toEqual({
      prompt_tokens: 8,
      completion_tokens: 4,
      cache_creation_tokens: 1,
      cache_read_tokens: 2,
    })
    expect(store.get(sessionTokenUsageAtom)).toEqual({
      inputTokens: 8,
      outputTokens: 4,
      cost: 0,
    })
  })

  it('hydrates usage from values state when the streamed message omits usage metadata', () => {
    const streamedMessage = new AIMessage({ id: 'assistant-usage-3', content: 'done' })
    const stateMessage = new AIMessage({
      id: 'assistant-usage-3',
      content: 'done',
      usage_metadata: {
        input_tokens: 15,
        output_tokens: 9,
        total_tokens: 24,
      },
    })
    mocks.stream.messages = [streamedMessage]
    mocks.stream.values = { messages: [stateMessage] }

    renderUsageHook()

    expect(lastConverterOptions()?.messages[0]?.additional_kwargs?.metadata?.usage).toEqual({
      prompt_tokens: 15,
      completion_tokens: 9,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
    })
  })
})
