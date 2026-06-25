import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AIMessage, HumanMessage, type BaseMessage } from '@langchain/core/messages'
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
  onCancel: () => Promise<void>
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
    convertedMessages: [] as {
      id: string
      role?: string
      content?: unknown
      metadata?: unknown
    }[],
    createMoldyAgentTransport: vi.fn(
      (
        conversationId: string,
        agentId: string,
        options?: { onState?: (state: unknown) => void },
      ) => {
        void conversationId
        void agentId
        void options
        return {
          kind: 'transport',
          setRunStartAcceptedListener: vi.fn(),
        }
      },
    ),
    useStream: vi.fn(() => stream),
    useChannel: vi.fn(() => []),
    useChannelEffect: vi.fn(),
    useExternalMessageConverter: vi.fn((options: { messages: readonly unknown[] }) => {
      void options
      return mocks.convertedMessages
    }),
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
    mocks.stream.isLoading = false
    mocks.stream.submit.mockReset()
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

  it('restores the current transcript when edit submission fails', async () => {
    const originalUserMessage = new HumanMessage({
      id: 'rollback-edit-user',
      content: 'original prompt',
      additional_kwargs: { metadata: { checkpoint_id: 'ck-after-original-user' } },
    })
    const staleAssistantMessage = new AIMessage({
      id: 'rollback-edit-assistant',
      content: 'old answer',
    })
    const submitFailure = new Error('submit failed')
    mocks.stream.messages = [originalUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [{ id: 'rollback-edit-user' }, { id: 'rollback-edit-assistant' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['rollback-edit-user', { parentCheckpointId: 'ck-before-original-user' }]]),
    )
    mocks.stream.submit.mockRejectedValueOnce(submitFailure)

    const runtimeOptions = renderRuntimeOptions()
    let thrown: unknown
    await act(async () => {
      try {
        await runtimeOptions.onEdit({
          content: [{ type: 'text', text: 'edited prompt' }],
          parentId: 'rollback-edit-user',
          sourceId: 'rollback-edit-user',
        })
      } catch (caught) {
        thrown = caught
      }
    })

    expect(thrown).toBe(submitFailure)
    expect(mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: [originalUserMessage, staleAssistantMessage],
      }),
    )
  })

  it('restores the current transcript when reload submission fails', async () => {
    const userMessage = new HumanMessage({
      id: 'rollback-reload-user',
      content: 'prompt',
      additional_kwargs: { metadata: { checkpoint_id: 'ck-after-reload-user' } },
    })
    const assistantMessage = new AIMessage({
      id: 'rollback-reload-assistant',
      content: 'old answer',
    })
    const submitFailure = new Error('reload failed')
    mocks.stream.messages = [userMessage, assistantMessage]
    mocks.convertedMessages = [{ id: 'rollback-reload-user' }, { id: 'rollback-reload-assistant' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['rollback-reload-assistant', { parentCheckpointId: 'ck-after-reload-user' }]]),
    )
    mocks.stream.submit.mockRejectedValueOnce(submitFailure)

    const runtimeOptions = renderRuntimeOptions()
    let thrown: unknown
    await act(async () => {
      try {
        await runtimeOptions.onReload('rollback-reload-user')
      } catch (caught) {
        thrown = caught
      }
    })

    expect(thrown).toBe(submitFailure)
    expect(mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: [userMessage, assistantMessage],
      }),
    )
  })

  it('restores the current transcript when canceling an edit run', async () => {
    const originalUserMessage = new HumanMessage({
      id: 'cancel-edit-user',
      content: 'original prompt',
      additional_kwargs: { metadata: { checkpoint_id: 'ck-after-cancel-user' } },
    })
    const staleAssistantMessage = new AIMessage({
      id: 'cancel-edit-assistant',
      content: 'old answer',
    })
    mocks.stream.messages = [originalUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [{ id: 'cancel-edit-user' }, { id: 'cancel-edit-assistant' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['cancel-edit-user', { parentCheckpointId: 'ck-before-cancel-user' }]]),
    )

    const runtimeOptions = renderRuntimeOptions()
    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'edited prompt' }],
        parentId: 'cancel-edit-user',
        sourceId: 'cancel-edit-user',
      })
    })
    expect(mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: expect.arrayContaining([expect.objectContaining({ content: 'edited prompt' })]),
      }),
    )

    await act(async () => {
      await runtimeOptions.onCancel()
    })

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(converterOptions.messages).toEqual(
      expect.arrayContaining([originalUserMessage, staleAssistantMessage]),
    )
    expect(converterOptions.messages.some((message) => message.content === 'edited prompt')).toBe(
      false,
    )
  })

  it('prefers the assistant parent checkpoint when assistant-ui passes the assistant id', async () => {
    mocks.convertedMessages = [{ id: 'user-1' }, { id: 'assistant-1' }]
    mocks.stream.messages = [
      new HumanMessage({
        id: 'user-1',
        content: 'prompt',
        additional_kwargs: { metadata: { checkpoint_id: 'ck-leaf-with-old-assistant' } },
      }),
      new AIMessage({
        id: 'assistant-1',
        content: 'old answer',
        additional_kwargs: { metadata: { checkpoint_id: 'ck-after-assistant-1' } },
      }),
    ]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['assistant-1', { parentCheckpointId: 'ck-after-user-1' }]]),
    )

    const runtimeOptions = renderRuntimeOptions()
    await runtimeOptions.onReload('assistant-1')

    expect(mocks.stream.submit).toHaveBeenCalledWith(null, { forkFrom: 'ck-after-user-1' })
  })

  it('clears sticky completed messages before edit shrinks history intentionally', async () => {
    const userMessage = new HumanMessage({ id: 'sticky-edit-user', content: 'original prompt' })
    const assistantMessage = new AIMessage({ id: 'sticky-edit-assistant', content: 'old answer' })
    mocks.stream.messages = [userMessage, assistantMessage]
    mocks.convertedMessages = [{ id: 'sticky-edit-user' }, { id: 'sticky-edit-assistant' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['sticky-edit-user', { parentCheckpointId: 'ck-before-user' }]]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-sticky-edit',
        }),
      { wrapper: createQueryWrapper() },
    )
    expect(mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: [userMessage, assistantMessage],
      }),
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'edited prompt' }],
        parentId: 'sticky-edit-user',
        sourceId: 'sticky-edit-user',
      })
    })

    mocks.stream.messages = [userMessage]
    mocks.convertedMessages = [{ id: 'sticky-edit-user' }]
    rerender()

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(converterOptions.messages).toHaveLength(1)
    expect(converterOptions.messages[0]).toEqual(
      expect.objectContaining({
        id: 'sticky-edit-user',
        content: 'edited prompt',
      }),
    )
    expect(mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: expect.not.arrayContaining([assistantMessage]),
      }),
    )
  })

  it('removes a stale assistant reply immediately when the edited user message is already replaced', async () => {
    const originalUserMessage = new HumanMessage({
      id: 'same-id-edit-user',
      content: 'original prompt',
      additional_kwargs: {
        metadata: {
          checkpoint_id: 'ck-after-same-id-original',
          branches: ['same-id-old', 'same-id-edit-user'],
          siblingCheckpointIds: ['ck-old', 'ck-after-same-id-original'],
          branchIndex: 1,
          branchTotal: 2,
        },
      },
    })
    const editedUserMessage = new HumanMessage({
      id: 'same-id-edit-user',
      content: 'edited prompt',
      additional_kwargs: {
        metadata: {
          checkpoint_id: 'ck-after-same-id-original',
          branches: ['same-id-old', 'same-id-edit-user'],
          siblingCheckpointIds: ['ck-old', 'ck-after-same-id-original'],
          branchIndex: 1,
          branchTotal: 2,
        },
      },
    })
    const staleAssistantMessage = new AIMessage({
      id: 'same-id-edit-assistant',
      content: 'stale answer',
    })
    const newAssistantMessage = new AIMessage({
      id: 'same-id-edit-assistant',
      content: 'new streamed answer',
    })
    mocks.stream.messages = [originalUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [{ id: 'same-id-edit-user' }, { id: 'same-id-edit-assistant' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['same-id-edit-user', { parentCheckpointId: 'ck-before-same-id-user' }]]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-same-id-edit',
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'edited prompt' }],
        parentId: 'same-id-edit-user',
        sourceId: 'same-id-edit-user',
      })
    })

    mocks.stream.isLoading = true
    mocks.stream.messages = [editedUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [{ id: 'same-id-edit-user' }]
    rerender()

    const staleTailConverterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(staleTailConverterOptions.messages).toEqual([
      expect.objectContaining({
        id: 'same-id-edit-user',
        content: 'edited prompt',
      }),
    ])
    expect(staleTailConverterOptions.messages[0]?.additional_kwargs?.metadata).toEqual(
      expect.objectContaining({
        branchIndex: 2,
        branchTotal: 3,
        moldyBranchPickerDisplayOnly: true,
      }),
    )
    expect(staleTailConverterOptions.messages).not.toContain(staleAssistantMessage)

    mocks.stream.messages = [editedUserMessage, newAssistantMessage]
    mocks.convertedMessages = [{ id: 'same-id-edit-user' }, { id: 'same-id-edit-assistant' }]
    rerender()

    const newTailConverterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(newTailConverterOptions.messages).toHaveLength(2)
    expect(newTailConverterOptions.messages[0]).toEqual(
      expect.objectContaining({
        id: 'same-id-edit-user',
        content: 'edited prompt',
      }),
    )
    expect(newTailConverterOptions.messages[1]).toBe(newAssistantMessage)
  })

  it('does not reuse stale hydrated branch metadata for the next same-id edit', async () => {
    const secondUserMessage = new HumanMessage({
      id: 'same-id-edit-user',
      content: 'second prompt',
    })
    const staleAssistantMessage = new AIMessage({
      id: 'same-id-edit-assistant',
      content: 'second answer',
    })
    mocks.stream.messages = [secondUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [{ id: 'same-id-edit-user' }, { id: 'same-id-edit-assistant' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['same-id-edit-user', { parentCheckpointId: 'ck-before-same-id-user' }]]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-same-id-edit',
        }),
      { wrapper: createQueryWrapper() },
    )

    const transportOptions = mocks.createMoldyAgentTransport.mock.calls.at(-1)?.[2] as
      | { onState?: (state: unknown) => void }
      | undefined
    act(() => {
      transportOptions?.onState?.({
        values: {
          messages: [
            {
              id: 'same-id-edit-user',
              type: 'human',
              content: 'second prompt',
              additional_kwargs: {
                metadata: {
                  branches: ['first-user', 'same-id-edit-user', 'third-user'],
                  siblingCheckpointIds: ['ck-first', 'ck-second', 'ck-third'],
                  activeBranchId: 'same-id-edit-user',
                  branchIndex: 1,
                  branchTotal: 3,
                },
              },
            },
            {
              id: 'same-id-edit-assistant',
              type: 'ai',
              content: 'second answer',
            },
          ],
        },
      })
    })

    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { readonly messages: readonly BaseMessage[] }
        | undefined
      expect(converterOptions?.messages[0]?.additional_kwargs?.metadata).toEqual(
        expect.objectContaining({
          branchIndex: 1,
          branchTotal: 3,
        }),
      )
    })

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'third prompt' }],
        parentId: 'same-id-edit-user',
        sourceId: 'same-id-edit-user',
      })
    })

    mocks.stream.isLoading = true
    mocks.stream.messages = [secondUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [{ id: 'same-id-edit-user' }]
    rerender()

    const pendingEditConverterOptions = mocks.useExternalMessageConverter.mock.calls.at(
      -1,
    )?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(pendingEditConverterOptions.messages[0]).toEqual(
      expect.objectContaining({
        id: 'same-id-edit-user',
        content: 'third prompt',
      }),
    )
    expect(pendingEditConverterOptions.messages[0]?.additional_kwargs?.metadata).toEqual(
      expect.objectContaining({
        branchIndex: 3,
        branchTotal: 4,
        moldyBranchPickerDisplayOnly: true,
      }),
    )
  })

  it('suppresses stale server branch metadata that arrives while a same-id edit is streaming', async () => {
    const secondUserMessage = new HumanMessage({
      id: 'same-id-streaming-user',
      content: 'second prompt',
    })
    const staleAssistantMessage = new AIMessage({
      id: 'same-id-streaming-assistant',
      content: 'second answer',
    })
    mocks.stream.messages = [secondUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [
      { id: 'same-id-streaming-user', role: 'user' },
      { id: 'same-id-streaming-assistant', role: 'assistant' },
    ]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['same-id-streaming-user', { parentCheckpointId: 'ck-before-user' }]]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-same-id-streaming-edit',
        }),
      { wrapper: createQueryWrapper() },
    )
    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'third prompt' }],
        parentId: 'same-id-streaming-user',
        sourceId: 'same-id-streaming-user',
      })
    })

    const transportOptions = mocks.createMoldyAgentTransport.mock.calls.at(-1)?.[2] as
      | { onState?: (state: unknown) => void }
      | undefined
    act(() => {
      transportOptions?.onState?.({
        values: {
          messages: [
            {
              id: 'same-id-streaming-user',
              type: 'human',
              content: 'second prompt',
              additional_kwargs: {
                metadata: {
                  branches: ['first-user', 'same-id-streaming-user', 'third-user'],
                  siblingCheckpointIds: ['ck-first', 'ck-second', 'ck-third'],
                  branchIndex: 1,
                  branchTotal: 3,
                },
              },
            },
          ],
        },
      })
    })

    mocks.stream.isLoading = true
    mocks.stream.messages = [secondUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [{ id: 'same-id-streaming-user', role: 'user' }]
    rerender()

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(converterOptions.messages[0]).toEqual(
      expect.objectContaining({
        id: 'same-id-streaming-user',
        content: 'third prompt',
      }),
    )
    expect(converterOptions.messages[0]?.additional_kwargs?.metadata).toEqual(
      expect.objectContaining({
        branchIndex: 1,
        branchTotal: 2,
        moldyBranchPickerDisplayOnly: true,
      }),
    )
  })

  it('suppresses stale converted branch metadata while a same-id edit is streaming', async () => {
    const secondUserMessage = new HumanMessage({
      id: 'same-id-converted-user',
      content: 'second prompt',
    })
    const staleAssistantMessage = new AIMessage({
      id: 'same-id-converted-assistant',
      content: 'second answer',
    })
    mocks.stream.messages = [secondUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [
      { id: 'same-id-converted-user', role: 'user', content: 'second prompt' },
      { id: 'same-id-converted-assistant', role: 'assistant', content: 'second answer' },
    ]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['same-id-converted-user', { parentCheckpointId: 'ck-before-user' }]]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-same-id-converted-edit',
        }),
      { wrapper: createQueryWrapper() },
    )
    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    mocks.convertedMessages = [
      {
        id: 'same-id-converted-user',
        role: 'user',
        content: 'third prompt',
        metadata: {
          custom: {
            branchIndex: 1,
            branchTotal: 3,
            siblingCheckpointIds: ['ck-first', 'ck-second', 'ck-third'],
            usage: { total_tokens: 7 },
          },
        },
      },
    ]
    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'third prompt' }],
        parentId: 'same-id-converted-user',
        sourceId: 'same-id-converted-user',
      })
    })

    mocks.stream.isLoading = true
    mocks.stream.messages = [secondUserMessage, staleAssistantMessage]
    rerender()

    const finalRuntimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly { readonly metadata?: unknown }[]
    }
    expect(finalRuntimeOptions.messages[0]?.metadata).toEqual({
      custom: {
        activeBranchId: 'pending-edit-1',
        branchCheckpointId: 'pending-edit-1',
        branchIndex: 1,
        branchTotal: 2,
        branches: ['pending-edit-0', 'pending-edit-1'],
        checkpoint_id: 'pending-edit-1',
        moldyBranchPickerDisplayOnly: true,
        siblingCheckpointIds: ['pending-edit-0', 'pending-edit-1'],
        usage: { total_tokens: 7 },
      },
    })
  })

  it('hydrates the completed active branch after a same-id edit run finishes', async () => {
    const secondUserMessage = new HumanMessage({
      id: 'same-id-complete-user',
      content: 'second prompt',
    })
    const visualAssistantMessage = new AIMessage({
      id: 'same-id-complete-assistant',
      content: 'visual stream fixture complete.',
    })
    mocks.stream.messages = [secondUserMessage]
    mocks.convertedMessages = [{ id: 'same-id-complete-user', role: 'user' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['same-id-complete-user', { parentCheckpointId: 'ck-before-user' }]]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-same-id-complete-edit',
        }),
      { wrapper: createQueryWrapper() },
    )
    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'third prompt' }],
        parentId: 'same-id-complete-user',
        sourceId: 'same-id-complete-user',
      })
    })

    mocks.stream.isLoading = true
    mocks.stream.messages = [secondUserMessage, visualAssistantMessage]
    mocks.convertedMessages = [
      { id: 'same-id-complete-user', role: 'user', content: 'third prompt' },
      {
        id: 'same-id-complete-assistant',
        role: 'assistant',
        content: 'visual stream fixture complete.',
      },
    ]
    rerender()

    mocks.apiFetch.mockClear()
    mocks.apiFetch.mockResolvedValueOnce({
      values: {
        messages: [
          {
            id: 'same-id-complete-user',
            type: 'human',
            content: 'third prompt',
          },
          {
            id: 'same-id-complete-assistant',
            type: 'ai',
            content: 'visual stream fixture complete.',
          },
        ],
      },
      metadata: {},
    })
    mocks.stream.isLoading = false
    mocks.stream.messages = [secondUserMessage, visualAssistantMessage]
    mocks.convertedMessages = [
      { id: 'same-id-complete-user', role: 'user', content: 'second prompt' },
      {
        id: 'same-id-complete-assistant',
        role: 'assistant',
        content: 'visual stream fixture complete.',
      },
    ]
    rerender()

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/conversations/conversation-same-id-complete-edit/langgraph/threads/conversation-same-id-complete-edit/state',
      )
    })
    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { readonly messages: readonly BaseMessage[] }
        | undefined
      expect(converterOptions?.messages[0]).toEqual(
        expect.objectContaining({
          id: 'same-id-complete-user',
          content: 'third prompt',
        }),
      )
    })
  })

  it('hydrates a completed edit even when the loading transition is missed', async () => {
    const secondUserMessage = new HumanMessage({
      id: 'missed-loading-user',
      content: 'second prompt',
    })
    const staleAssistantMessage = new AIMessage({
      id: 'missed-loading-assistant',
      content: 'second answer',
    })
    mocks.stream.messages = [secondUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [
      { id: 'missed-loading-user', role: 'user', content: 'second prompt' },
      {
        id: 'missed-loading-assistant',
        role: 'assistant',
        content: 'second answer',
      },
    ]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['missed-loading-user', { parentCheckpointId: 'ck-before-user' }]]),
    )
    mocks.apiFetch.mockReset()
    mocks.apiFetch
      .mockResolvedValueOnce({
        values: {
          messages: [
            {
              id: 'missed-loading-user',
              type: 'human',
              content: 'third prompt',
            },
          ],
        },
        metadata: {},
      })
      .mockResolvedValueOnce({
        values: {
          messages: [
            {
              id: 'missed-loading-user',
              type: 'human',
              content: 'third prompt',
            },
            {
              id: 'missed-loading-assistant',
              type: 'ai',
              content: 'third answer',
            },
          ],
        },
        metadata: {},
      })

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-missed-loading-edit',
        }),
      { wrapper: createQueryWrapper() },
    )
    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions

    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'third prompt' }],
        parentId: 'missed-loading-user',
        sourceId: 'missed-loading-user',
      })
    })

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledTimes(2)
    })
    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { readonly messages: readonly BaseMessage[] }
        | undefined
      expect(converterOptions?.messages).toEqual([
        expect.objectContaining({
          id: 'missed-loading-user',
          content: 'third prompt',
        }),
        expect.objectContaining({
          id: 'missed-loading-assistant',
          content: 'third answer',
        }),
      ])
    })
  })

  it('renders an edited user message in place while the forked run appends optimistic messages', async () => {
    const originalUserMessage = new HumanMessage({
      id: 'optimistic-edit-user',
      content: 'original prompt',
      additional_kwargs: { metadata: { checkpoint_id: 'ck-after-original-user' } },
    })
    const staleAssistantMessage = new AIMessage({
      id: 'optimistic-edit-assistant',
      content: 'stale answer',
    })
    const optimisticEditedUserMessage = new HumanMessage({
      id: 'opt-edited-user',
      content: 'edited prompt',
    })
    const streamingAssistantMessage = new AIMessage({
      id: 'streaming-edited-assistant',
      content: 'new answer',
    })
    mocks.stream.messages = [originalUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [{ id: 'optimistic-edit-user' }, { id: 'optimistic-edit-assistant' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['optimistic-edit-user', { parentCheckpointId: 'ck-before-original-user' }]]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-optimistic-edit',
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'edited prompt' }],
        parentId: 'optimistic-edit-user',
        sourceId: 'optimistic-edit-user',
      })
    })

    mocks.stream.isLoading = true
    mocks.stream.messages = [
      originalUserMessage,
      staleAssistantMessage,
      optimisticEditedUserMessage,
      streamingAssistantMessage,
    ]
    mocks.convertedMessages = [{ id: 'optimistic-edit-user' }, { id: 'streaming-edited-assistant' }]
    rerender()

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(converterOptions.messages).toHaveLength(2)
    expect(converterOptions.messages[0]).toEqual(
      expect.objectContaining({
        id: 'optimistic-edit-user',
        content: 'edited prompt',
      }),
    )
    expect(converterOptions.messages[1]).toBe(streamingAssistantMessage)
    expect(converterOptions.messages).not.toContain(staleAssistantMessage)
    expect(converterOptions.messages).not.toContain(optimisticEditedUserMessage)
  })

  it('renders an edited user message in place when assistant-ui ids differ from server message ids', async () => {
    const serverUserMessage = new HumanMessage({
      id: 'server-edit-user',
      content: 'server prompt',
      additional_kwargs: { metadata: { checkpoint_id: 'ck-after-server-user' } },
    })
    const staleAssistantMessage = new AIMessage({
      id: 'server-edit-assistant',
      content: 'stale answer',
    })
    const optimisticEditedUserMessage = new HumanMessage({
      id: 'stream-edit-user',
      content: 'edited prompt',
    })
    const streamingAssistantMessage = new AIMessage({
      id: 'stream-edit-assistant',
      content: 'new answer',
    })
    mocks.stream.messages = [serverUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [{ id: 'stream-visible-user' }, { id: 'stream-visible-assistant' }]
    mocks.apiFetch.mockResolvedValue({
      values: {
        messages: [{ id: 'server-edit-user' }, { id: 'server-edit-assistant' }],
      },
      metadata: {
        parent_checkpoint_by_message_id: { 'server-edit-user': 'ck-before-server-user' },
        checkpoint_by_message_id: { 'server-edit-user': 'ck-after-server-user' },
      },
    })

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-optimistic-edit-mismatched-ids',
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'edited prompt' }],
        parentId: 'stream-visible-user',
        sourceId: 'stream-visible-user',
      })
    })

    mocks.stream.isLoading = true
    mocks.stream.messages = [
      serverUserMessage,
      staleAssistantMessage,
      optimisticEditedUserMessage,
      streamingAssistantMessage,
    ]
    mocks.convertedMessages = [{ id: 'stream-visible-user' }, { id: 'stream-edit-assistant' }]
    rerender()

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(converterOptions.messages).toHaveLength(2)
    expect(converterOptions.messages[0]).toEqual(
      expect.objectContaining({
        id: 'server-edit-user',
        content: 'edited prompt',
      }),
    )
    expect(converterOptions.messages[1]).toEqual(
      expect.objectContaining({
        content: 'new answer',
      }),
    )
    expect(converterOptions.messages).not.toContain(staleAssistantMessage)
    expect(converterOptions.messages).not.toContain(optimisticEditedUserMessage)
  })

  it('applies edit replacement when assistant-ui submits an edited message', async () => {
    const serverUserMessage = new HumanMessage({
      id: 'event-server-edit-user',
      content: 'server prompt',
      additional_kwargs: { metadata: { checkpoint_id: 'ck-after-event-server-user' } },
    })
    const staleAssistantMessage = new AIMessage({
      id: 'event-server-edit-assistant',
      content: 'stale answer',
    })
    const optimisticEditedUserMessage = new HumanMessage({
      id: 'event-stream-edit-user',
      content: 'edited prompt',
    })
    const streamingAssistantMessage = new AIMessage({
      id: 'event-stream-edit-assistant',
      content: 'new answer',
    })
    mocks.stream.messages = [serverUserMessage, staleAssistantMessage]
    mocks.convertedMessages = [{ id: 'event-visible-user' }, { id: 'event-visible-assistant' }]
    mocks.apiFetch.mockResolvedValue({
      values: {
        messages: [{ id: 'event-server-edit-user' }, { id: 'event-server-edit-assistant' }],
      },
      metadata: {
        parent_checkpoint_by_message_id: {
          'event-server-edit-user': 'ck-before-event-server-user',
        },
        checkpoint_by_message_id: { 'event-server-edit-user': 'ck-after-event-server-user' },
      },
    })

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-edit-submit-started',
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    await act(async () => {
      await runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'edited prompt' }],
        parentId: 'event-visible-user',
        sourceId: 'event-visible-user',
      })
    })

    mocks.stream.isLoading = true
    mocks.stream.messages = [
      serverUserMessage,
      staleAssistantMessage,
      optimisticEditedUserMessage,
      streamingAssistantMessage,
    ]
    mocks.convertedMessages = [{ id: 'event-visible-user' }, { id: 'event-stream-edit-assistant' }]
    rerender()

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(converterOptions.messages).toHaveLength(2)
    expect(converterOptions.messages[0]).toEqual(
      expect.objectContaining({
        id: 'event-server-edit-user',
        content: 'edited prompt',
      }),
    )
    expect(converterOptions.messages[1]).toBe(streamingAssistantMessage)
    expect(converterOptions.messages).not.toContain(staleAssistantMessage)
    expect(converterOptions.messages).not.toContain(optimisticEditedUserMessage)
  })

  it('removes the stale assistant reply immediately while a reload streams a replacement', async () => {
    const userMessage = new HumanMessage({
      id: 'reload-user',
      content: 'third prompt',
      additional_kwargs: {
        metadata: {
          checkpoint_id: 'ck-after-reload-user',
          branches: ['first-user', 'second-user', 'reload-user'],
          siblingCheckpointIds: ['ck-first', 'ck-second', 'ck-after-reload-user'],
          branchIndex: 2,
          branchTotal: 3,
        },
      },
    })
    const staleAssistantMessage = new AIMessage({
      id: 'reload-stale-assistant',
      content: 'visual stream fixture complete.',
    })
    const streamingAssistantMessage = new AIMessage({
      id: 'reload-streaming-assistant',
      content: 'E2E visual stream fixture is still running',
    })
    mocks.stream.messages = [userMessage, staleAssistantMessage]
    mocks.convertedMessages = [
      { id: 'reload-user', role: 'user' },
      { id: 'reload-stale-assistant', role: 'assistant' },
    ]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['reload-stale-assistant', { parentCheckpointId: 'ck-after-reload-user' }]]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-reload-streaming',
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    await act(async () => {
      await runtimeOptions.onReload('reload-stale-assistant')
    })

    mocks.stream.isLoading = true
    mocks.stream.messages = [userMessage, staleAssistantMessage, streamingAssistantMessage]
    mocks.convertedMessages = [
      { id: 'reload-user', role: 'user' },
      { id: 'reload-streaming-assistant', role: 'assistant' },
    ]
    rerender()

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(converterOptions.messages[0]).toBe(userMessage)
    expect(converterOptions.messages[1]).toEqual(
      expect.objectContaining({
        id: 'reload-streaming-assistant',
        content: 'E2E visual stream fixture is still running',
        additional_kwargs: expect.objectContaining({
          metadata: expect.objectContaining({
            branchIndex: 1,
            branchTotal: 2,
            moldyBranchPickerDisplayOnly: true,
          }),
        }),
      }),
    )
    expect(converterOptions.messages).not.toContain(staleAssistantMessage)
  })

  it('hydrates a completed reload even when the loading transition is missed', async () => {
    const userMessage = new HumanMessage({
      id: 'reload-hydrate-user',
      content: 'third prompt',
      additional_kwargs: {
        metadata: {
          checkpoint_id: 'ck-after-reload-hydrate-user',
          branches: ['first-user', 'second-user', 'reload-hydrate-user'],
          siblingCheckpointIds: ['ck-first', 'ck-second', 'ck-after-reload-hydrate-user'],
          branchIndex: 2,
          branchTotal: 3,
        },
      },
    })
    const staleAssistantMessage = new AIMessage({
      id: 'reload-hydrate-stale-assistant',
      content: 'visual stream fixture complete.',
    })
    mocks.stream.messages = [userMessage, staleAssistantMessage]
    mocks.convertedMessages = [
      { id: 'reload-hydrate-user', role: 'user' },
      { id: 'reload-hydrate-stale-assistant', role: 'assistant' },
    ]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([
        ['reload-hydrate-stale-assistant', { parentCheckpointId: 'ck-after-reload-hydrate-user' }],
      ]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-reload-hydrate',
        }),
      { wrapper: createQueryWrapper() },
    )
    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    mocks.apiFetch.mockReset()
    mocks.apiFetch
      .mockResolvedValueOnce({
        values: {
          messages: [
            {
              id: 'reload-hydrate-user',
              type: 'human',
              content: 'third prompt',
              additional_kwargs: userMessage.additional_kwargs,
            },
            {
              id: 'reload-hydrate-stale-assistant',
              type: 'ai',
              content: 'visual stream fixture complete.',
            },
          ],
        },
        metadata: {},
      })
      .mockResolvedValueOnce({
        values: {
          messages: [
            {
              id: 'reload-hydrate-user',
              type: 'human',
              content: 'third prompt',
              additional_kwargs: userMessage.additional_kwargs,
            },
            {
              id: 'reload-hydrate-new-assistant',
              type: 'ai',
              content: 'visual stream fixture complete.',
              additional_kwargs: {
                metadata: {
                  checkpoint_id: 'ck-after-reload-hydrate-new-assistant',
                  branches: ['reload-hydrate-stale-assistant', 'reload-hydrate-new-assistant'],
                  siblingCheckpointIds: [
                    'ck-after-reload-hydrate-stale-assistant',
                    'ck-after-reload-hydrate-new-assistant',
                  ],
                  branchIndex: 1,
                  branchTotal: 2,
                },
              },
            },
          ],
        },
        metadata: {},
      })

    await act(async () => {
      await runtimeOptions.onReload('reload-hydrate-stale-assistant')
    })
    rerender()

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledTimes(2)
    })
    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { readonly messages: readonly BaseMessage[] }
        | undefined
      expect(converterOptions?.messages).toEqual([
        expect.objectContaining({
          id: 'reload-hydrate-user',
          content: 'third prompt',
        }),
        expect.objectContaining({
          id: 'reload-hydrate-new-assistant',
          content: 'visual stream fixture complete.',
        }),
      ])
    })
  })

  it('waits for latest user branch metadata before hydrating a completed reload', async () => {
    const completeUserAdditionalKwargs = {
      metadata: {
        checkpoint_id: 'ck-after-reload-branch-user',
        branches: ['first-user', 'second-user', 'reload-branch-user'],
        siblingCheckpointIds: ['ck-first', 'ck-second', 'ck-after-reload-branch-user'],
        branchIndex: 2,
        branchTotal: 3,
      },
    }
    const userMessage = new HumanMessage({
      id: 'reload-branch-user',
      content: 'third prompt',
      additional_kwargs: completeUserAdditionalKwargs,
    })
    const staleAssistantMessage = new AIMessage({
      id: 'reload-branch-stale-assistant',
      content: 'visual stream fixture complete.',
    })
    mocks.stream.messages = [userMessage, staleAssistantMessage]
    mocks.convertedMessages = [
      { id: 'reload-branch-user', role: 'user' },
      { id: 'reload-branch-stale-assistant', role: 'assistant' },
    ]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([
        ['reload-branch-stale-assistant', { parentCheckpointId: 'ck-after-reload-branch-user' }],
      ]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-reload-branch-metadata',
        }),
      { wrapper: createQueryWrapper() },
    )
    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    mocks.apiFetch.mockReset()
    mocks.apiFetch
      .mockResolvedValueOnce({
        values: {
          messages: [
            {
              id: 'reload-branch-user',
              type: 'human',
              content: 'third prompt',
            },
            {
              id: 'reload-branch-new-assistant',
              type: 'ai',
              content: 'visual stream fixture complete.',
            },
          ],
        },
        metadata: {},
      })
      .mockResolvedValueOnce({
        values: {
          messages: [
            {
              id: 'reload-branch-user',
              type: 'human',
              content: 'third prompt',
              additional_kwargs: completeUserAdditionalKwargs,
            },
            {
              id: 'reload-branch-new-assistant',
              type: 'ai',
              content: 'visual stream fixture complete.',
            },
          ],
        },
        metadata: {},
      })

    await act(async () => {
      await runtimeOptions.onReload('reload-branch-stale-assistant')
    })
    rerender()

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledTimes(2)
    })
    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { readonly messages: readonly BaseMessage[] }
        | undefined
      expect(converterOptions?.messages[0]).toEqual(
        expect.objectContaining({
          id: 'reload-branch-user',
          additional_kwargs: expect.objectContaining({
            metadata: expect.objectContaining({
              branchIndex: 2,
              branchTotal: 3,
            }),
          }),
        }),
      )
    })
  })

  it('waits for the regenerated assistant branch to become the latest sibling', async () => {
    const userMessage = new HumanMessage({
      id: 'reload-assistant-branch-user',
      content: 'third prompt',
    })
    const staleAssistantMessage = new AIMessage({
      id: 'reload-assistant-stale',
      content: 'visual stream fixture complete.',
      additional_kwargs: {
        metadata: {
          checkpoint_id: 'ck-old-assistant',
          branches: ['reload-assistant-stale'],
          siblingCheckpointIds: ['ck-old-assistant'],
        },
      },
    })
    mocks.stream.messages = [userMessage, staleAssistantMessage]
    mocks.convertedMessages = [
      { id: 'reload-assistant-branch-user', role: 'user' },
      { id: 'reload-assistant-stale', role: 'assistant' },
    ]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['reload-assistant-stale', { parentCheckpointId: 'ck-after-user' }]]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-reload-assistant-branch-metadata',
        }),
      { wrapper: createQueryWrapper() },
    )
    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    mocks.apiFetch.mockReset()
    mocks.apiFetch
      .mockResolvedValueOnce({
        values: {
          messages: [
            {
              id: 'reload-assistant-branch-user',
              type: 'human',
              content: 'third prompt',
            },
            {
              id: 'reload-assistant-new',
              type: 'ai',
              content: 'visual stream fixture complete.',
              additional_kwargs: {
                metadata: {
                  checkpoint_id: 'ck-old-assistant',
                  branches: ['reload-assistant-new', 'reload-assistant-stale'],
                  siblingCheckpointIds: ['ck-new-assistant', 'ck-old-assistant'],
                  branchIndex: 0,
                  branchTotal: 2,
                },
              },
            },
          ],
        },
        metadata: {},
      })
      .mockResolvedValueOnce({
        values: {
          messages: [
            {
              id: 'reload-assistant-branch-user',
              type: 'human',
              content: 'third prompt',
            },
            {
              id: 'reload-assistant-new',
              type: 'ai',
              content: 'visual stream fixture complete.',
              additional_kwargs: {
                metadata: {
                  checkpoint_id: 'ck-new-assistant',
                  branches: ['reload-assistant-stale', 'reload-assistant-new'],
                  siblingCheckpointIds: ['ck-old-assistant', 'ck-new-assistant'],
                  branchIndex: 1,
                  branchTotal: 2,
                },
              },
            },
          ],
        },
        metadata: {},
      })

    await act(async () => {
      await runtimeOptions.onReload('reload-assistant-stale')
    })
    rerender()

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledTimes(2)
    })
    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { readonly messages: readonly BaseMessage[] }
        | undefined
      expect(converterOptions?.messages[1]).toEqual(
        expect.objectContaining({
          id: 'reload-assistant-new',
          additional_kwargs: expect.objectContaining({
            metadata: expect.objectContaining({
              branchIndex: 1,
              branchTotal: 2,
            }),
          }),
        }),
      )
    })
  })

  it('adds display-only branch metadata while a regenerated assistant streams without its prompt prefix', async () => {
    const userMessage = new HumanMessage({
      id: 'reload-optimistic-user',
      content: 'third prompt',
      additional_kwargs: { metadata: { checkpoint_id: 'ck-after-reload-optimistic-user' } },
    })
    const staleAssistantMessage = new AIMessage({
      id: 'reload-optimistic-stale-assistant',
      content: 'old answer',
      additional_kwargs: {
        metadata: {
          checkpoint_id: 'ck-after-reload-optimistic-stale-assistant',
        },
      },
    })
    mocks.stream.messages = [userMessage, staleAssistantMessage]
    mocks.convertedMessages = [
      { id: 'reload-optimistic-user', role: 'user' },
      { id: 'reload-optimistic-stale-assistant', role: 'assistant' },
    ]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([
        [
          'reload-optimistic-stale-assistant',
          { parentCheckpointId: 'ck-after-reload-optimistic-user' },
        ],
      ]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-reload-optimistic',
        }),
      { wrapper: createQueryWrapper() },
    )
    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions

    await act(async () => {
      await runtimeOptions.onReload('reload-optimistic-stale-assistant')
    })

    mocks.stream.isLoading = true
    mocks.stream.messages = [
      new AIMessage({
        id: 'reload-optimistic-new-assistant',
        content: 'partial new answer',
      }),
    ]
    mocks.convertedMessages = [{ id: 'reload-optimistic-new-assistant', role: 'assistant' }]
    rerender()

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
      readonly messages: readonly BaseMessage[]
    }
    expect(converterOptions.messages).toHaveLength(1)
    expect(converterOptions.messages[0]).toEqual(
      expect.objectContaining({
        id: 'reload-optimistic-new-assistant',
        additional_kwargs: expect.objectContaining({
          metadata: expect.objectContaining({
            branchIndex: 1,
            branchTotal: 2,
            moldyBranchPickerDisplayOnly: true,
            siblingCheckpointIds: ['pending-reload-0', 'pending-reload-1'],
          }),
        }),
      }),
    )
  })

  it('clears sticky completed messages before reload shrinks history intentionally', async () => {
    const userMessage = new HumanMessage({ id: 'sticky-reload-user', content: 'original prompt' })
    const assistantMessage = new AIMessage({
      id: 'sticky-reload-assistant',
      content: 'old answer',
    })
    mocks.stream.messages = [userMessage, assistantMessage]
    mocks.convertedMessages = [{ id: 'sticky-reload-user' }, { id: 'sticky-reload-assistant' }]
    mocks.metadataStore.getSnapshot.mockReturnValue(
      new Map([['sticky-reload-assistant', { parentCheckpointId: 'ck-after-user' }]]),
    )

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-sticky-reload',
        }),
      { wrapper: createQueryWrapper() },
    )
    expect(mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: [userMessage, assistantMessage],
      }),
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as RuntimeOptions
    await runtimeOptions.onReload('sticky-reload-user')

    mocks.stream.messages = [userMessage]
    mocks.convertedMessages = [{ id: 'sticky-reload-user' }]
    rerender()

    expect(mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: [userMessage],
      }),
    )
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

  it('waits for server checkpoint metadata when an edit starts right after text appears', async () => {
    vi.useFakeTimers()
    try {
      mocks.convertedMessages = [{ id: 'user-1' }, { id: 'assistant-1' }]
      mocks.apiFetch.mockResolvedValueOnce({ metadata: {} }).mockResolvedValueOnce({
        values: {
          messages: [
            { type: 'human', id: 'user-1' },
            { type: 'ai', id: 'assistant-1' },
          ],
        },
        metadata: {
          parent_checkpoint_by_message_id: { 'user-1': 'ck-root-after-delay' },
          checkpoint_by_message_id: { 'user-1': 'ck-after-user-1' },
        },
      })

      const runtimeOptions = renderRuntimeOptions()
      const editPromise = runtimeOptions.onEdit({
        content: [{ type: 'text', text: 'edited first prompt' }],
        parentId: 'user-1',
        sourceId: 'user-1',
      })
      await vi.advanceTimersByTimeAsync(250)
      await editPromise

      expect(mocks.apiFetch.mock.calls.length).toBeGreaterThanOrEqual(2)
      expect(mocks.stream.submit).toHaveBeenCalledWith(
        { messages: [expect.objectContaining({ content: 'edited first prompt' })] },
        { forkFrom: 'ck-root-after-delay' },
      )
    } finally {
      vi.useRealTimers()
    }
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
        checkpoint_by_message_id: { 'user-1': 'ck-leaf-with-old-assistant' },
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
    mocks.apiFetch.mockResolvedValue({
      values: {
        messages: [
          { type: 'human', id: 'unrelated-user' },
          { type: 'ai', id: 'unrelated-assistant' },
        ],
      },
      metadata: {},
    })

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
