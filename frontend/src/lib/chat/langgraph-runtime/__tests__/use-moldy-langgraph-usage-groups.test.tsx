import { AIMessage, ToolMessage } from '@langchain/core/messages'
import { createStore } from 'jotai'
import { beforeEach, describe, expect, it } from 'vitest'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import {
  lastConverterOptions,
  mocks,
  renderUsageHook,
  resetUsageMocks,
  usageEvent,
} from './use-moldy-langgraph-usage-test-harness'

describe('useMoldyLangGraphStream grouped usage', () => {
  beforeEach(resetUsageMocks)

  it('copies exact final-message usage to the first assistant message in a tool group', () => {
    mocks.stream.messages = [
      new AIMessage({
        id: 'assistant-tool-call',
        content: '',
        tool_calls: [{ id: 'call-docx', name: 'execute_in_skill', args: {} }],
      }),
      new ToolMessage({
        id: 'tool-docx',
        name: 'execute_in_skill',
        tool_call_id: 'call-docx',
        content: 'OUTPUT_FILES: report.md',
      }),
      new AIMessage({ id: 'assistant-final', content: 'done' }),
    ]
    mocks.stream.values = { messages: mocks.stream.messages }
    mocks.useChannel.mockImplementation((_stream, channels: readonly string[]) =>
      channels.includes('custom:usage') ? [usageEvent('assistant-final')] : [],
    )

    renderUsageHook()

    const usage = {
      prompt_tokens: 12,
      completion_tokens: 5,
      cache_creation_tokens: 2,
      cache_read_tokens: 3,
      estimated_cost: 0.22,
    }
    expect(lastConverterOptions()?.messages[0]?.additional_kwargs?.metadata?.usage).toEqual(usage)
    expect(lastConverterOptions()?.messages[2]?.additional_kwargs?.metadata?.usage).toEqual(usage)
  })

  it('replaces run-level usage with assistant-message usage to avoid double counting', () => {
    const assistantMessage = new AIMessage({ id: 'assistant-final', content: 'done' })
    mocks.stream.messages = [assistantMessage]
    mocks.stream.values = { messages: [assistantMessage] }
    mocks.useChannel.mockImplementation((_stream, channels: readonly string[]) =>
      channels.includes('custom:usage') ? [usageEvent(''), usageEvent('assistant-final')] : [],
    )

    const store = createStore()
    renderUsageHook(store)

    expect(store.get(sessionTokenUsageAtom)).toEqual({
      inputTokens: 12,
      outputTokens: 5,
      cost: 0.22,
    })
  })
})
