import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AIMessage } from '@langchain/core/messages'
import { Provider, createStore } from 'jotai'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import { useMoldyLangGraphStream } from '../use-moldy-langgraph-stream'

interface MockInterrupt {
  readonly id: string
  readonly value: unknown
}

interface MockStream {
  messages: unknown[]
  values: { messages: unknown[] }
  interrupts: MockInterrupt[]
  isLoading: boolean
  submit: ReturnType<typeof vi.fn>
  respond: ReturnType<typeof vi.fn>
  stop: ReturnType<typeof vi.fn>
}

type ChannelEffectOptions = {
  readonly onEvent: (event: unknown) => void
}

type ConverterOptions = {
  readonly messages: readonly {
    readonly additional_kwargs?: {
      readonly metadata?: {
        readonly usage?: unknown
      }
    }
  }[]
}

const mocks = vi.hoisted(() => {
  const stream: MockStream = {
    messages: [],
    values: { messages: [] },
    interrupts: [],
    isLoading: false,
    submit: vi.fn(),
    respond: vi.fn(),
    stop: vi.fn(),
  }
  return {
    stream,
    createMoldyAgentTransport: vi.fn(() => ({ kind: 'transport' })),
    useStream: vi.fn(() => stream),
    useChannel: vi.fn(() => []),
    useChannelEffect: vi.fn(),
    useExternalMessageConverter: vi.fn((options: ConverterOptions) => {
      void options
      return [{ id: 'converted' }]
    }),
    useExternalStoreRuntime: vi.fn((options: unknown) => ({ kind: 'runtime', options })),
    convertLangChainBaseMessage: vi.fn(),
  }
})

vi.mock('../moldy-agent-transport', () => ({
  createMoldyAgentTransport: mocks.createMoldyAgentTransport,
}))

vi.mock('@langchain/react', () => ({
  useChannel: mocks.useChannel,
  useChannelEffect: mocks.useChannelEffect,
  useStream: mocks.useStream,
}))

vi.mock('@assistant-ui/react', () => ({
  useExternalMessageConverter: mocks.useExternalMessageConverter,
  useExternalStoreRuntime: mocks.useExternalStoreRuntime,
}))

vi.mock('@assistant-ui/react-langchain', () => ({
  convertLangChainBaseMessage: mocks.convertLangChainBaseMessage,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

vi.mock('sonner', () => ({
  toast: {
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

function usageEvent() {
  return {
    type: 'event',
    method: 'custom:usage',
    event_id: 'usage-event-1',
    seq: 10,
    run_id: 'run-usage',
    params: {
      namespace: [],
      data: {
        assistant_msg_id: 'assistant-usage-1',
        run_id: 'run-usage',
        prompt_tokens: 12,
        completion_tokens: 5,
        cache_creation_tokens: 2,
        cache_read_tokens: 3,
        estimated_cost: 0.22,
      },
    },
  }
}

describe('useMoldyLangGraphStream usage events', () => {
  beforeEach(() => {
    mocks.stream.messages = []
    mocks.stream.values = { messages: [] }
    mocks.stream.interrupts = []
    mocks.stream.submit.mockClear()
    mocks.stream.respond.mockClear()
    mocks.stream.stop.mockClear()
    mocks.useChannelEffect.mockClear()
    mocks.useExternalMessageConverter.mockClear()
  })

  it('attaches v3 usage events to assistant-ui message metadata and session totals', () => {
    const assistantMessage = new AIMessage({ id: 'assistant-usage-1', content: 'done' })
    mocks.stream.messages = [assistantMessage]
    mocks.stream.values = { messages: [assistantMessage] }

    const store = createStore()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <Provider store={store}>{children}</Provider>
      </QueryClientProvider>
    )

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-usage',
          conversationId: 'conversation-usage',
        }),
      { wrapper },
    )

    const effectOptions = mocks.useChannelEffect.mock.calls
      .map((call) => call[2])
      .filter((value): value is ChannelEffectOptions => {
        return typeof value === 'object' && value !== null && 'onEvent' in value
      })

    act(() => {
      for (const options of effectOptions) {
        options.onEvent(usageEvent())
      }
    })

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
      | ConverterOptions
      | undefined
    expect(converterOptions?.messages[0]?.additional_kwargs?.metadata?.usage).toEqual({
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
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <Provider store={store}>{children}</Provider>
      </QueryClientProvider>
    )

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-usage',
          conversationId: 'conversation-usage',
        }),
      { wrapper },
    )

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
      | ConverterOptions
      | undefined
    expect(converterOptions?.messages[0]?.additional_kwargs?.metadata?.usage).toEqual({
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
})
