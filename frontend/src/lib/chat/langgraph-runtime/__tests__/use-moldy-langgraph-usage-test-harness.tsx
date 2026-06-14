import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider, createStore } from 'jotai'
import type { ReactNode } from 'react'
import { vi } from 'vitest'
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
  respondAll: ReturnType<typeof vi.fn>
  stop: ReturnType<typeof vi.fn>
  getThread: ReturnType<typeof vi.fn>
}

export type ConverterOptions = {
  readonly messages: readonly {
    readonly additional_kwargs?: {
      readonly metadata?: {
        readonly usage?: unknown
      }
    }
    readonly content?: unknown
  }[]
}

export type RuntimeOptions = {
  readonly messages: readonly unknown[]
}

type MockUseChannel = (_stream: MockStream, channels: readonly string[]) => unknown[]
type TestStore = ReturnType<typeof createStore>

const hoistedMocks = vi.hoisted(() => {
  const STREAM_CONTROLLER = Symbol('STREAM_CONTROLLER')
  const metadataStore = {
    subscribe: vi.fn(() => () => {}),
    getSnapshot: vi.fn(() => new Map()),
  }
  const stream = {
    messages: [],
    values: { messages: [] },
    interrupts: [],
    isLoading: false,
    submit: vi.fn(),
    respond: vi.fn(),
    respondAll: vi.fn(),
    stop: vi.fn(),
    getThread: vi.fn(() => ({ interrupts: [], subscribe: vi.fn() })),
    [STREAM_CONTROLLER]: { messageMetadataStore: metadataStore },
  } as MockStream & {
    [STREAM_CONTROLLER]: { messageMetadataStore: typeof metadataStore }
  }
  return {
    STREAM_CONTROLLER,
    metadataStore,
    stream,
    createMoldyAgentTransport: vi.fn(() => ({ kind: 'transport' })),
    useStream: vi.fn(() => stream),
    useChannel: vi.fn<MockUseChannel>(() => []),
    useChannelEffect: vi.fn(),
    useExternalMessageConverter: vi.fn((options: ConverterOptions) => {
      void options
      return [{ id: 'converted' }]
    }),
    useExternalStoreRuntime: vi.fn((options: unknown) => ({ kind: 'runtime', options })),
    convertLangChainBaseMessage: vi.fn(),
  }
})

export const mocks = hoistedMocks

vi.mock('../moldy-agent-transport', () => ({
  createMoldyAgentTransport: hoistedMocks.createMoldyAgentTransport,
}))

vi.mock('@langchain/react', () => ({
  STREAM_CONTROLLER: hoistedMocks.STREAM_CONTROLLER,
  useChannel: hoistedMocks.useChannel,
  useChannelEffect: hoistedMocks.useChannelEffect,
  useStream: hoistedMocks.useStream,
}))

vi.mock('@assistant-ui/react', () => ({
  useExternalMessageConverter: hoistedMocks.useExternalMessageConverter,
  useExternalStoreRuntime: hoistedMocks.useExternalStoreRuntime,
}))

vi.mock('@assistant-ui/react-langchain', () => ({
  convertLangChainBaseMessage: hoistedMocks.convertLangChainBaseMessage,
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

export function resetUsageMocks(): void {
  mocks.stream.messages = []
  mocks.stream.values = { messages: [] }
  mocks.stream.interrupts = []
  mocks.stream.submit.mockClear()
  mocks.stream.respond.mockClear()
  mocks.stream.respondAll.mockClear()
  mocks.stream.stop.mockClear()
  mocks.stream.getThread.mockClear()
  mocks.stream.getThread.mockReturnValue({ interrupts: [], subscribe: vi.fn() })
  mocks.metadataStore.getSnapshot.mockReturnValue(new Map())
  mocks.useChannel.mockReset()
  mocks.useChannel.mockReturnValue([])
  mocks.useChannelEffect.mockClear()
  mocks.useExternalMessageConverter.mockClear()
}

export function renderUsageHook(
  store: TestStore = createStore(),
  conversationId = 'conversation-usage',
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <Provider store={store}>{children}</Provider>
    </QueryClientProvider>
  )
  const hook = renderHook(
    (props: { readonly activeConversationId?: string } | undefined) =>
      useMoldyLangGraphStream({
        agentId: 'agent-usage',
        conversationId: props?.activeConversationId ?? conversationId,
      }),
    { initialProps: { activeConversationId: conversationId }, wrapper },
  )
  return { ...hook, store }
}

export function lastConverterOptions(): ConverterOptions | undefined {
  return mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as ConverterOptions | undefined
}

export function lastRuntimeOptions(): RuntimeOptions | undefined {
  return mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions | undefined
}

export function usageEvent(assistantMsgId = 'assistant-usage-1') {
  return {
    type: 'event',
    method: 'custom',
    event_id: 'usage-event-1',
    seq: 10,
    run_id: 'run-usage',
    params: {
      namespace: [],
      data: {
        name: 'usage',
        payload: {
          assistant_msg_id: assistantMsgId,
          run_id: 'run-usage',
          prompt_tokens: 12,
          completion_tokens: 5,
          cache_creation_tokens: 2,
          cache_read_tokens: 3,
          estimated_cost: 0.22,
        },
      },
    },
  }
}

export function usagePayload(assistantMsgId = 'assistant-usage-1') {
  return usageEvent(assistantMsgId).params.data
}

export function messageStartEvent(messageId = 'assistant-usage-4') {
  return {
    type: 'event',
    method: 'messages',
    event_id: 'message-start-1',
    seq: 20,
    params: {
      namespace: [],
      data: [
        {
          event: 'message-start',
          role: 'ai',
          id: messageId,
        },
        { run_id: 'run-message-usage' },
      ],
    },
  }
}

export function messageFinishEvent() {
  return {
    type: 'event',
    method: 'messages',
    event_id: 'message-finish-1',
    seq: 21,
    params: {
      namespace: [],
      data: [
        {
          event: 'message-finish',
          usage: { input_tokens: 18, output_tokens: 7, total_tokens: 25 },
        },
        { run_id: 'run-message-usage' },
      ],
    },
  }
}
