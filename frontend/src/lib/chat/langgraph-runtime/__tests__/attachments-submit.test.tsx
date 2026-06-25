import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMoldyLangGraphStream } from '../use-moldy-langgraph-stream'

interface MockStream {
  readonly messages: unknown[]
  readonly values: { readonly messages: unknown[] }
  readonly interrupts: unknown[]
  readonly isLoading: boolean
  readonly submit: ReturnType<typeof vi.fn>
  readonly respond: ReturnType<typeof vi.fn>
  readonly respondAll: ReturnType<typeof vi.fn>
  readonly stop: ReturnType<typeof vi.fn>
}

type RuntimeOptions = {
  readonly onNew: (message: {
    readonly content: readonly unknown[]
    readonly attachments?: readonly {
      readonly id: string
      readonly content?: readonly unknown[]
    }[]
  }) => Promise<void>
}

const mocks = vi.hoisted(() => {
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
    [STREAM_CONTROLLER]: { messageMetadataStore: metadataStore },
  } as MockStream & {
    [STREAM_CONTROLLER]: { messageMetadataStore: typeof metadataStore }
  }
  return {
    STREAM_CONTROLLER,
    metadataStore,
    stream,
    createMoldyAgentTransport: vi.fn((conversationId: string) => ({
      kind: 'transport',
      conversationId,
      setRunStartAcceptedListener: vi.fn(),
    })),
    useStream: vi.fn(() => stream),
    useChannel: vi.fn(() => []),
    useChannelEffect: vi.fn(),
    useExternalMessageConverter: vi.fn((options: { readonly messages: readonly unknown[] }) => {
      void options
      return []
    }),
    useExternalStoreRuntime: vi.fn((options: unknown) => ({ kind: 'runtime', options })),
    convertLangChainBaseMessage: vi.fn(),
  }
})

vi.mock('../moldy-agent-transport', () => ({
  createMoldyAgentTransport: mocks.createMoldyAgentTransport,
}))

vi.mock('@langchain/react', () => ({
  STREAM_CONTROLLER: mocks.STREAM_CONTROLLER,
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

function createQueryWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function QueryWrapper({ children }: { readonly children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

function isRuntimeOptions(value: unknown): value is RuntimeOptions {
  return typeof value === 'object' && value !== null && 'onNew' in value
}

function latestRuntimeOptions(): RuntimeOptions {
  const options = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]
  if (!isRuntimeOptions(options)) throw new Error('runtime options were not captured')
  return options
}

describe('useMoldyLangGraphStream attachments', () => {
  beforeEach(() => {
    mocks.stream.submit.mockClear()
    mocks.stream.stop.mockClear()
    mocks.metadataStore.getSnapshot.mockReturnValue(new Map())
    mocks.useExternalStoreRuntime.mockClear()
  })

  it('submits uploaded attachment ids through the shared LangGraph stream input', async () => {
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-attachments',
          conversationId: 'conversation-attachments',
        }),
      { wrapper: createQueryWrapper() },
    )

    await latestRuntimeOptions().onNew({
      content: [{ type: 'text', text: 'Please review this.' }],
      attachments: [
        {
          id: 'upload-1',
          content: [{ type: 'text', text: '[attachment: plan.pdf](/api/uploads/upload-1)' }],
        },
      ],
    })

    expect(mocks.stream.submit).toHaveBeenCalledWith({
      messages: [
        expect.objectContaining({
          content: 'Please review this.[attachment: plan.pdf](/api/uploads/upload-1)',
        }),
      ],
      attachments: [{ id: 'upload-1' }],
    })
  })
})
