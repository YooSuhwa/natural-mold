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
    apiFetch: vi.fn(),
  }
})

vi.mock('../moldy-agent-transport', () => ({
  createMoldyAgentTransport: mocks.createMoldyAgentTransport,
}))

vi.mock('@/lib/api/client', () => ({
  apiFetch: mocks.apiFetch,
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
    mocks.apiFetch.mockReset()
    mocks.apiFetch.mockResolvedValue({ metadata: {} })
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

  it('falls back to server parent checkpoint metadata for first-message edits', async () => {
    mocks.convertedMessages = [{ id: 'user-1' }, { id: 'assistant-1' }]
    mocks.apiFetch.mockResolvedValue({
      metadata: {
        parent_checkpoint_by_message_id: { 'user-1': 'ck-root' },
        checkpoint_by_message_id: { 'user-1': 'ck-after-user-1' },
      },
    })

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onEdit({
      content: [{ type: 'text', text: 'edited first prompt' }],
      parentId: 'user-1',
      sourceId: 'user-1',
    })

    expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/conversations/conversation-1/langgraph/threads/conversation-1/state',
    )
    expect(mocks.stream.submit).toHaveBeenCalledWith(
      { messages: [expect.objectContaining({ content: 'edited first prompt' })] },
      { forkFrom: 'ck-root' },
    )
  })

  it('merges server parent checkpoint metadata with same-id local metadata for edits', async () => {
    mocks.convertedMessages = [{ id: 'user-1' }, { id: 'assistant-1' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['user-1', { parentCheckpointId: undefined }]]),
    )
    mocks.apiFetch.mockResolvedValue({
      metadata: {
        parent_checkpoint_by_message_id: { 'user-1': 'ck-root' },
        checkpoint_by_message_id: { 'user-1': 'ck-after-user-1' },
      },
    })

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onEdit({
      content: [{ type: 'text', text: 'edited first prompt' }],
      parentId: 'user-1',
      sourceId: 'user-1',
    })

    expect(mocks.stream.submit).toHaveBeenCalledWith(
      { messages: [expect.objectContaining({ content: 'edited first prompt' })] },
      { forkFrom: 'ck-root' },
    )
  })

  it('keeps server parent checkpoint when local metadata has the same id without one', async () => {
    mocks.convertedMessages = [{ id: 'user-1' }, { id: 'assistant-1' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['user-1', { parentCheckpointId: undefined }]]),
    )
    mocks.apiFetch.mockResolvedValue({
      metadata: {
        parent_checkpoint_by_message_id: { 'user-1': 'ck-root' },
        checkpoint_by_message_id: { 'user-1': 'ck-after-user-1' },
      },
    })

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onEdit({
      content: [{ type: 'text', text: 'edited first prompt' }],
      parentId: null,
      sourceId: 'user-1',
    })

    expect(mocks.stream.submit).toHaveBeenCalledWith(
      { messages: [expect.objectContaining({ content: 'edited first prompt' })] },
      { forkFrom: 'ck-root' },
    )
  })

  it('falls back to parentId when sourceId is stale for edits', async () => {
    mocks.convertedMessages = [{ id: 'user-1' }, { id: 'assistant-1' }]
    mocks.apiFetch.mockResolvedValue({
      metadata: {
        parent_checkpoint_by_message_id: { 'user-1': 'ck-root' },
        checkpoint_by_message_id: { 'user-1': 'ck-after-user-1' },
      },
    })

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onEdit({
      content: [{ type: 'text', text: 'edited first prompt' }],
      parentId: 'user-1',
      sourceId: 'stale-source-id',
    })

    expect(mocks.stream.submit).toHaveBeenCalledWith(
      { messages: [expect.objectContaining({ content: 'edited first prompt' })] },
      { forkFrom: 'ck-root' },
    )
  })

  it('falls back by message index when local edit ids differ from server state ids', async () => {
    mocks.convertedMessages = [{ id: 'local-user-1' }, { id: 'local-assistant-1' }]
    mocks.apiFetch.mockResolvedValue({
      values: {
        messages: [{ id: 'server-user-1' }, { id: 'server-assistant-1' }],
      },
      metadata: {
        parent_checkpoint_by_message_id: { 'server-user-1': 'ck-root' },
        checkpoint_by_message_id: { 'server-user-1': 'ck-after-user-1' },
      },
    })

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onEdit({
      content: [{ type: 'text', text: 'edited first prompt' }],
      parentId: 'local-user-1',
      sourceId: 'local-user-1',
    })

    expect(mocks.stream.submit).toHaveBeenCalledWith(
      { messages: [expect.objectContaining({ content: 'edited first prompt' })] },
      { forkFrom: 'ck-root' },
    )
  })

  it('falls back by visible index when assistant-ui edit ids are temporary', async () => {
    mocks.convertedMessages = [{ id: 'stream-user-1' }, { id: 'stream-assistant-1' }]
    mocks.apiFetch.mockResolvedValue({
      values: {
        messages: [{ id: 'server-user-1' }, { id: 'server-assistant-1' }],
      },
      metadata: {
        parent_checkpoint_by_message_id: { 'server-user-1': 'ck-root' },
        checkpoint_by_message_id: { 'server-user-1': 'ck-after-user-1' },
      },
    })

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onEdit({
      content: [{ type: 'text', text: 'edited first prompt' }],
      parentId: 'stream-user-1',
      sourceId: 'stream-user-1',
    })

    expect(mocks.stream.submit).toHaveBeenCalledWith(
      { messages: [expect.objectContaining({ content: 'edited first prompt' })] },
      { forkFrom: 'ck-root' },
    )
  })

  it('falls back to server parent checkpoint metadata for regenerate', async () => {
    mocks.convertedMessages = [{ id: 'user-1' }, { id: 'assistant-1' }]
    mocks.apiFetch.mockResolvedValue({
      metadata: {
        parent_checkpoint_by_message_id: { 'assistant-1': 'ck-after-user-1' },
        checkpoint_by_message_id: { 'user-1': 'ck-after-user-1' },
      },
    })

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onReload('user-1')

    expect(mocks.stream.submit).toHaveBeenCalledWith(null, { forkFrom: 'ck-after-user-1' })
  })

  it('aligns server fallback indexes when tool result messages are grouped into assistant UI messages', async () => {
    mocks.convertedMessages = [
      { id: 'local-user-1' },
      { id: 'local-assistant-tool-call' },
      { id: 'local-assistant-final' },
    ]
    mocks.apiFetch.mockResolvedValue({
      values: {
        messages: [
          { type: 'human', id: 'server-user-1' },
          { type: 'ai', id: 'server-assistant-tool-call' },
          { type: 'tool', id: 'server-tool-result' },
          { type: 'ai', id: 'server-assistant-final' },
        ],
      },
      metadata: {
        parent_checkpoint_by_message_id: {
          'server-assistant-final': 'ck-after-tool-result',
        },
        checkpoint_by_message_id: {
          'server-tool-result': 'ck-after-tool-result',
          'server-assistant-final': 'ck-after-final',
        },
      },
    })

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onReload('local-assistant-tool-call')

    expect(mocks.stream.submit).toHaveBeenCalledWith(null, { forkFrom: 'ck-after-tool-result' })
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
