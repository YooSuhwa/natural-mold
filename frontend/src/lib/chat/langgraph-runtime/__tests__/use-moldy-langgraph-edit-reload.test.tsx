import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { BaseMessage } from '@langchain/core/messages'
import type { MessageMetadataMap } from '@langchain/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMoldyLangGraphStream } from '../use-moldy-langgraph-stream'

interface MockStream {
  messages: BaseMessage[]
  values: { messages: BaseMessage[] }
  interrupts: never[]
  isLoading: boolean
  submit: ReturnType<typeof vi.fn>
  respond: ReturnType<typeof vi.fn>
  stop: ReturnType<typeof vi.fn>
}

type RuntimeOptions = {
  onEdit: (message: {
    content: readonly { type: 'text'; text: string }[]
    attachments?: readonly { id?: unknown; content?: readonly unknown[] }[]
    parentId: string | null
    sourceId: string | null
  }) => Promise<void>
  onReload: (parentId: string | null) => Promise<void>
}

const mocks = vi.hoisted(() => {
  const STREAM_CONTROLLER = Symbol('STREAM_CONTROLLER')
  const metadataStore = {
    subscribe: vi.fn(() => () => {}),
    getSnapshot: vi.fn((): MessageMetadataMap => new Map()),
  }
  const stream: MockStream & {
    [STREAM_CONTROLLER]: { messageMetadataStore: typeof metadataStore }
  } = {
    messages: [],
    values: { messages: [] },
    interrupts: [],
    isLoading: false,
    submit: vi.fn(),
    respond: vi.fn(),
    stop: vi.fn(),
    [STREAM_CONTROLLER]: { messageMetadataStore: metadataStore },
  }
  return {
    STREAM_CONTROLLER,
    metadataStore,
    stream,
    convertedMessages: [] as { id: string }[],
    createMoldyAgentTransport: vi.fn(() => ({ kind: 'transport' })),
    useStream: vi.fn(() => stream),
    useChannel: vi.fn(() => []),
    useChannelEffect: vi.fn(),
    useExternalMessageConverter: vi.fn(() => mocks.convertedMessages),
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
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

function renderRuntimeOptions(): RuntimeOptions {
  renderHook(
    () =>
      useMoldyLangGraphStream({
        agentId: 'agent-1',
        conversationId: 'conversation-1',
      }),
    { wrapper: createQueryWrapper() },
  )
  return mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
}

function messageWithCheckpoint(id: string, checkpointId: string): BaseMessage {
  return {
    id,
    additional_kwargs: { metadata: { checkpoint_id: checkpointId } },
  } as unknown as BaseMessage
}

describe('useMoldyLangGraphStream edit and reload checkpoint forks', () => {
  beforeEach(() => {
    mocks.stream.messages = []
    mocks.stream.values = { messages: [] }
    mocks.stream.submit.mockClear()
    mocks.metadataStore.getSnapshot.mockReturnValue(new Map())
    mocks.useExternalStoreRuntime.mockClear()
    mocks.convertedMessages = []
  })

  it('edits by forking from the source message parent checkpoint metadata', async () => {
    mocks.convertedMessages = [{ id: 'user-1' }, { id: 'assistant-1' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['user-1', { parentCheckpointId: 'ck-before-user-1' }]]),
    )

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onEdit({
      content: [{ type: 'text', text: 'edited prompt' }],
      parentId: 'user-1',
      sourceId: 'user-1',
    })

    expect(mocks.stream.submit).toHaveBeenCalledWith(
      { messages: [expect.objectContaining({ content: 'edited prompt' })] },
      { forkFrom: 'ck-before-user-1' },
    )
  })

  it('regenerates by forking from the assistant target parent checkpoint metadata', async () => {
    mocks.convertedMessages = [{ id: 'user-1' }, { id: 'assistant-1' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['assistant-1', { parentCheckpointId: 'ck-after-user-1' }]]),
    )

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onReload('user-1')

    expect(mocks.stream.submit).toHaveBeenCalledWith(null, { forkFrom: 'ck-after-user-1' })
  })

  it('falls back to hydrated message checkpoint metadata when live metadata is absent', async () => {
    mocks.convertedMessages = [{ id: 'user-0' }, { id: 'user-1' }, { id: 'assistant-1' }]
    mocks.stream.messages = [messageWithCheckpoint('user-0', 'ck-after-user-0')]

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onEdit({
      content: [{ type: 'text', text: 'edited first follow-up' }],
      parentId: 'user-1',
      sourceId: 'user-1',
    })

    expect(mocks.stream.submit).toHaveBeenCalledWith(
      { messages: [expect.objectContaining({ content: 'edited first follow-up' })] },
      { forkFrom: 'ck-after-user-0' },
    )
  })

  it('does not submit edit or reload when no safe checkpoint is available', async () => {
    mocks.convertedMessages = [{ id: 'user-1' }, { id: 'assistant-1' }]

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onEdit({
      content: [{ type: 'text', text: 'edited prompt' }],
      parentId: 'user-1',
      sourceId: 'user-1',
    })
    await runtimeOptions.onReload('user-1')

    expect(mocks.stream.submit).not.toHaveBeenCalled()
  })
})
