import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AIMessage, HumanMessage } from '@langchain/core/messages'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMoldyLangGraphStream } from '../use-moldy-langgraph-stream'
import { dispatchMoldyBranchSwitched } from '../branch-switch-events'
import type { AttachmentAdapter, CompleteAttachment, PendingAttachment } from '@assistant-ui/react'

interface MockInterrupt {
  id: string
  value: unknown
}

interface MockThreadInterrupt {
  interruptId: string
  namespace: string[]
  payload: unknown
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
  disconnect: ReturnType<typeof vi.fn>
  getThread: ReturnType<typeof vi.fn>
}

interface MockUseStreamOptions {
  transport: unknown
  threadId: string
  onCreated?: (run: { runId: string }) => void
  onCompleted?: () => void
}

interface MockTransportOptions {
  onState?: (state: unknown) => void
  onRunStartAccepted?: () => void
}

interface MockTransport {
  kind: 'transport'
  conversationId: string
  onState?: (state: unknown) => void
  setRunStartAcceptedListener: ReturnType<typeof vi.fn>
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
    disconnect: vi.fn(),
    getThread: vi.fn(),
    [STREAM_CONTROLLER]: { messageMetadataStore: metadataStore },
  } as MockStream & {
    [STREAM_CONTROLLER]: { messageMetadataStore: typeof metadataStore }
  }
  const lifecycleSubscription = { unsubscribe: vi.fn() }
  const thread = {
    interrupts: [] as MockThreadInterrupt[],
    subscribe: vi.fn(async () => lifecycleSubscription),
  }
  stream.getThread.mockReturnValue(thread)
  return {
    STREAM_CONTROLLER,
    lifecycleSubscription,
    metadataStore,
    stream,
    thread,
    createMoldyAgentTransport: vi.fn(
      (conversationId: string, _agentId: string, options?: MockTransportOptions) =>
        ({
          kind: 'transport',
          conversationId,
          onState: options?.onState,
          setRunStartAcceptedListener: vi.fn(),
        }) satisfies MockTransport,
    ),
    useStream: vi.fn((options: MockUseStreamOptions) => {
      void options
      return stream
    }),
    useChannel: vi.fn(() => []),
    useChannelEffect: vi.fn(),
    useExternalMessageConverter: vi.fn((options: { messages: readonly unknown[] }) => {
      void options
      return [{ id: 'converted' }] as { id: string; role?: string; content?: unknown }[]
    }),
    useExternalStoreRuntime: vi.fn((options: unknown) => ({ kind: 'runtime', options })),
    convertLangChainBaseMessage: vi.fn(),
    apiFetch: vi.fn(),
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

vi.mock('@/lib/api/client', () => ({
  apiFetch: mocks.apiFetch,
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

describe('useMoldyLangGraphStream', () => {
  beforeEach(() => {
    mocks.stream.messages = []
    mocks.stream.values = { messages: [] }
    mocks.stream.interrupts = []
    mocks.stream.isLoading = false
    mocks.stream.submit.mockReset()
    mocks.stream.respond.mockClear()
    mocks.stream.respondAll.mockClear()
    mocks.stream.stop.mockClear()
    mocks.stream.disconnect.mockClear()
    mocks.stream.getThread.mockClear()
    mocks.stream.getThread.mockReturnValue(mocks.thread)
    mocks.thread.interrupts = []
    mocks.thread.subscribe.mockClear()
    mocks.thread.subscribe.mockResolvedValue(mocks.lifecycleSubscription)
    mocks.lifecycleSubscription.unsubscribe.mockClear()
    mocks.metadataStore.getSnapshot.mockReturnValue(new Map())
    mocks.createMoldyAgentTransport.mockClear()
    mocks.useChannelEffect.mockClear()
    mocks.useExternalMessageConverter.mockClear()
    mocks.useExternalStoreRuntime.mockClear()
    mocks.apiFetch.mockReset()
    mocks.apiFetch.mockResolvedValue({ metadata: {}, values: { messages: [] } })
  })

  it('creates one LangChain stream and bridges it into assistant-ui', () => {
    const feedbackAdapter = { submit: vi.fn() }
    const attachmentAdapter: AttachmentAdapter = {
      accept: 'image/*',
      add: vi.fn(
        async (state: { file: File }): Promise<PendingAttachment> => ({
          id: 'pending-attachment',
          type: 'file',
          name: state.file.name,
          contentType: state.file.type,
          file: state.file,
          status: { type: 'requires-action', reason: 'composer-send' },
        }),
      ),
      send: vi.fn(
        async (attachment): Promise<CompleteAttachment> => ({
          ...attachment,
          status: { type: 'complete' },
        }),
      ),
      remove: vi.fn(async () => {}),
    }

    const { result } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-1',
          conversationId: 'conversation-1',
          feedbackAdapter,
          attachmentAdapter,
        }),
      { wrapper: createQueryWrapper() },
    )

    expect(mocks.createMoldyAgentTransport).toHaveBeenCalledWith(
      'conversation-1',
      'agent-1',
      expect.objectContaining({ onState: expect.any(Function) }),
    )
    expect(mocks.useStream).toHaveBeenCalledWith(
      expect.objectContaining({
        transport: expect.objectContaining({
          kind: 'transport',
          conversationId: 'conversation-1',
        }),
        threadId: 'conversation-1',
      }),
    )
    expect(mocks.useExternalStoreRuntime).toHaveBeenCalledWith(
      expect.objectContaining({
        messages: [{ id: 'converted' }],
        isRunning: false,
        adapters: {
          feedback: feedbackAdapter,
          attachments: attachmentAdapter,
        },
      }),
    )
    expect(result.current.stream).toBe(mocks.stream)
    expect(result.current.activities).toEqual([])
    expect(result.current.deepAgentsState).toEqual({ todos: [], files: [] })
    expect(result.current.assistantRuntime).toEqual(expect.objectContaining({ kind: 'runtime' }))
  })

  it('defers transport state hydration emitted during initial render until after mount', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    mocks.useStream.mockImplementationOnce((options: MockUseStreamOptions) => {
      const transport = options.transport as MockTransportOptions
      setTimeout(() => {
        transport.onState?.({
          metadata: {
            latest_run: { id: 'run-render-hydration', status: 'stale' },
          },
        })
      }, 0)
      return mocks.stream
    })

    try {
      renderHook(
        () =>
          useMoldyLangGraphStream({
            agentId: 'agent-hydrate-render',
            conversationId: 'conversation-hydrate-render',
          }),
        { wrapper: createQueryWrapper() },
      )

      await waitFor(() => {
        const options = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
          messages: readonly { id?: string; content?: unknown }[]
        }
        expect(options.messages).toEqual([
          expect.objectContaining({
            id: 'moldy-stale-run-render-hydration',
            content: 'stale',
          }),
        ])
      })

      expect(
        consoleError.mock.calls.some((call) =>
          call.some(
            (item) =>
              typeof item === 'string' &&
              item.includes(
                "Can't perform a React state update on a component that hasn't mounted yet",
              ),
          ),
        ),
      ).toBe(false)
    } finally {
      consoleError.mockRestore()
    }
  })

  it('keeps assistant-ui running while a submitted user turn is waiting for the first assistant token', () => {
    mocks.stream.isLoading = true
    mocks.useExternalMessageConverter.mockReturnValue([
      { id: 'pending-user', role: 'user', content: [{ type: 'text', text: '안녕?' }] },
    ])

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-running',
          conversationId: 'conversation-running',
        }),
      { wrapper: createQueryWrapper() },
    )

    expect(mocks.useExternalStoreRuntime).toHaveBeenCalledWith(
      expect.objectContaining({
        isRunning: true,
        messages: [
          { id: 'pending-user', role: 'user', content: [{ type: 'text', text: '안녕?' }] },
        ],
      }),
    )
  })

  it('keeps a converted ask_user tool-call card when the same assistant message previously rendered as text only', () => {
    const assistantSourceTextOnly = new AIMessage({
      id: 'assistant-ask-user-cache',
      content: '네, 골라봐요!',
    })
    const assistantSourceWithToolCall = new AIMessage({
      id: 'assistant-ask-user-cache',
      content: '네, 골라봐요!',
      tool_calls: [
        {
          id: 'call_e2e_ask_user_fruit',
          name: 'ask_user',
          args: {
            approval_id: 'call_e2e_ask_user_fruit',
            hitl_interrupt_id: 'intr-ask-user',
            hitl_action_index: 0,
            hitl_total_actions: 1,
            allowed_decisions: ['respond'],
            mode: 'option_list',
            title: '입력이 필요합니다',
            question: '어떤 과일이 좋아요?',
            options: [{ id: 'apple', label: '🍎 사과' }],
          },
        },
      ],
    })
    const textOnlyAssistant = [
      {
        id: 'assistant-ask-user-cache',
        role: 'assistant',
        content: [{ type: 'text', text: '네, 골라봐요!' }],
      },
    ]
    const pendingAskUserAssistant = [
      {
        id: 'assistant-ask-user-cache',
        role: 'assistant',
        status: { type: 'requires-action', reason: 'tool-calls' },
        content: [
          { type: 'text', text: '네, 골라봐요!' },
          {
            type: 'tool-call',
            toolCallId: 'call_e2e_ask_user_fruit',
            toolName: 'ask_user',
            args: {
              approval_id: 'call_e2e_ask_user_fruit',
              hitl_interrupt_id: 'intr-ask-user',
              hitl_action_index: 0,
              hitl_total_actions: 1,
              allowed_decisions: ['respond'],
              mode: 'option_list',
              title: '입력이 필요합니다',
              question: '어떤 과일이 좋아요?',
              options: [{ id: 'apple', label: '🍎 사과' }],
            },
          },
        ],
      },
    ]
    mocks.useExternalMessageConverter
      .mockReturnValueOnce(textOnlyAssistant)
      .mockReturnValue(pendingAskUserAssistant)
    mocks.stream.messages = [assistantSourceTextOnly]

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-ask-user-cache',
          conversationId: 'conversation-ask-user-cache',
        }),
      { wrapper: createQueryWrapper() },
    )

    expect(mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({ messages: textOnlyAssistant }),
    )

    mocks.stream.messages = [assistantSourceWithToolCall]
    rerender()

    expect(mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: [
          expect.objectContaining({
            id: 'assistant-ask-user-cache',
            status: { type: 'requires-action', reason: 'tool-calls' },
            content: expect.arrayContaining([
              expect.objectContaining({
                type: 'tool-call',
                toolCallId: 'call_e2e_ask_user_fruit',
                toolName: 'ask_user',
              }),
            ]),
          }),
        ],
      }),
    )
  })

  it('renders terminal stale state from LangGraph hydration as a localized assistant notice', async () => {
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-stale',
          conversationId: 'conversation-stale',
        }),
      { wrapper: createQueryWrapper() },
    )

    const transport = mocks.createMoldyAgentTransport.mock.results.at(-1)?.value as {
      onState?: (state: unknown) => void
    }
    await act(async () => {
      transport.onState?.({
        metadata: {
          latest_run: { id: 'run-stale', status: 'stale' },
        },
      })
    })

    await waitFor(() => {
      const options = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as {
        messages: readonly { id?: string; content?: unknown }[]
      }
      expect(options.messages).toEqual([
        expect.objectContaining({
          id: 'moldy-stale-run-stale',
          content: 'stale',
        }),
      ])
    })
  })

  it('submits new assistant-ui messages through the same LangChain stream', async () => {
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-2',
          conversationId: 'conversation-2',
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as {
      onNew: (message: { content: { type: string; text: string }[] }) => Promise<void>
      onCancel: () => Promise<void>
    }
    await runtimeOptions.onNew({ content: [{ type: 'text', text: 'hello' }] })
    await runtimeOptions.onCancel()

    expect(mocks.stream.submit).toHaveBeenCalledWith({
      messages: [expect.objectContaining({ content: 'hello' })],
    })
    expect(mocks.stream.stop).toHaveBeenCalled()
  })

  it('starts post-run hydration polling when a run completes normally', async () => {
    mocks.stream.isLoading = false
    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hydrate',
          conversationId: 'conversation-hydrate',
        }),
      { wrapper: createQueryWrapper() },
    )
    // Flush the mount microtask that resets wasLoadingRef, then enter a run.
    await act(async () => {})
    mocks.stream.isLoading = true
    rerender()
    await act(async () => {})
    mocks.apiFetch.mockClear()
    // Run completes (no cancel) -> hydration polling should query thread state.
    mocks.stream.isLoading = false
    rerender()
    await waitFor(() => {
      expect(mocks.apiFetch.mock.calls.some(([path]) => String(path).endsWith('/state'))).toBe(true)
    })
  })

  it('does not start post-run hydration polling when the user cancels the run', async () => {
    mocks.stream.isLoading = false
    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-cancel',
          conversationId: 'conversation-cancel',
        }),
      { wrapper: createQueryWrapper() },
    )
    await act(async () => {})
    mocks.stream.isLoading = true
    rerender()
    await act(async () => {})

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as {
      onCancel: () => Promise<void>
    }
    mocks.apiFetch.mockClear()
    // Cancel: stop the stream, which flips isLoading false on the next render.
    await act(async () => {
      const cancelled = runtimeOptions.onCancel()
      mocks.stream.isLoading = false
      rerender()
      await cancelled
    })
    // Give any (incorrectly scheduled) hydration microtask/poll a chance to run.
    await act(async () => {})
    await act(async () => {})

    expect(mocks.apiFetch.mock.calls.some(([path]) => String(path).endsWith('/state'))).toBe(false)
  })

  it('runs the before-submit callback before submitting new assistant-ui messages', async () => {
    const callOrder: string[] = []
    const onBeforeSubmit = vi.fn(() => {
      callOrder.push('before')
    })
    mocks.stream.submit.mockImplementationOnce(async () => {
      callOrder.push('submit')
    })

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-2',
          conversationId: 'conversation-2',
          onBeforeSubmit,
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as {
      onNew: (message: { content: { type: string; text: string }[] }) => Promise<void>
    }
    await runtimeOptions.onNew({ content: [{ type: 'text', text: 'hello' }] })

    expect(onBeforeSubmit).toHaveBeenCalledOnce()
    expect(callOrder).toEqual(['before', 'submit'])
  })

  it('does not run the before-submit callback for blank assistant-ui messages', async () => {
    const onBeforeSubmit = vi.fn()

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-2',
          conversationId: 'conversation-2',
          onBeforeSubmit,
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as {
      onNew: (message: { content: { type: string; text: string }[] }) => Promise<void>
    }
    await act(async () => {
      await runtimeOptions.onNew({ content: [{ type: 'text', text: '   ' }] })
    })

    expect(onBeforeSubmit).not.toHaveBeenCalled()
  })

  it('keeps a submitted user message visible until the stream echoes it', async () => {
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-2',
          conversationId: 'conversation-2',
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as {
      onNew: (message: { content: { type: string; text: string }[] }) => Promise<void>
    }
    await act(async () => {
      await runtimeOptions.onNew({ content: [{ type: 'text', text: '사과를 골라줘' }] })
    })

    await waitFor(() => {
      const sawPendingUser = mocks.useExternalMessageConverter.mock.calls.some(([options]) => {
        const messages = (options as { messages?: readonly unknown[] }).messages ?? []
        return messages.some(
          (message) => HumanMessage.isInstance(message) && message.content === '사과를 골라줘',
        )
      })
      expect(sawPendingUser).toBe(true)
    })
  })

  it('passes a run-start accepted callback to the LangGraph transport', () => {
    const onRunStartAccepted = vi.fn()

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-2',
          conversationId: 'conversation-2',
          onRunStartAccepted,
        }),
      { wrapper: createQueryWrapper() },
    )

    expect(mocks.createMoldyAgentTransport).toHaveBeenCalledWith(
      'conversation-2',
      'agent-2',
      expect.objectContaining({ onState: expect.any(Function) }),
    )
    const transport = mocks.createMoldyAgentTransport.mock.results[0]?.value as
      | MockTransport
      | undefined
    expect(transport?.setRunStartAcceptedListener).toHaveBeenCalledWith(onRunStartAccepted)
  })

  it('keeps the LangGraph transport stable when only the run-start callback changes', async () => {
    const firstCallback = vi.fn()
    const secondCallback = vi.fn()

    const { rerender } = renderHook(
      ({ callback }: { callback: () => void }) =>
        useMoldyLangGraphStream({
          agentId: 'agent-2',
          conversationId: 'conversation-2',
          onRunStartAccepted: callback,
        }),
      {
        initialProps: { callback: firstCallback },
        wrapper: createQueryWrapper(),
      },
    )

    const transport = mocks.createMoldyAgentTransport.mock.results[0]?.value as
      | MockTransport
      | undefined
    await act(async () => {
      rerender({ callback: secondCallback })
    })

    expect(mocks.createMoldyAgentTransport).toHaveBeenCalledOnce()
    const listenerCall = transport?.setRunStartAcceptedListener.mock.calls
      .toReversed()
      .find((call): call is [() => void] => typeof call[0] === 'function')
    listenerCall?.[0]()
    expect(firstCallback).not.toHaveBeenCalled()
    expect(secondCallback).toHaveBeenCalledOnce()
  })

  it('keeps the completed assistant message when SDK history briefly shrinks to a prefix', () => {
    const userMessage = new HumanMessage('hello')
    const assistantMessage = new AIMessage('complete response')
    mocks.stream.messages = [userMessage, assistantMessage]

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-sticky',
          conversationId: 'conversation-sticky',
        }),
      { wrapper: createQueryWrapper() },
    )

    expect(mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: [userMessage, assistantMessage],
      }),
    )

    mocks.stream.messages = [userMessage]
    rerender()

    expect(mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: [userMessage, assistantMessage],
      }),
    )
  })

  it('keeps cached converted history when assistant-ui conversion briefly shrinks during a new run', () => {
    const firstUser = new HumanMessage({ id: 'user-1', content: '안녕?' })
    const firstAssistant = new AIMessage({ id: 'assistant-1', content: '안녕하세요!' })
    const secondUser = new HumanMessage({ id: 'user-2', content: '반가워' })
    const secondAssistant = new AIMessage({ id: 'assistant-2', content: '반갑습니다!' })
    const readyMessages = [firstUser, firstAssistant, secondUser, secondAssistant]
    const readyConvertedMessages = [
      { id: 'user-1', role: 'user', content: [{ type: 'text', text: '안녕?' }] },
      { id: 'assistant-1', role: 'assistant', content: [{ type: 'text', text: '안녕하세요!' }] },
      { id: 'user-2', role: 'user', content: [{ type: 'text', text: '반가워' }] },
      { id: 'assistant-2', role: 'assistant', content: [{ type: 'text', text: '반갑습니다!' }] },
    ]
    mocks.stream.messages = readyMessages
    mocks.useExternalMessageConverter.mockReturnValue(readyConvertedMessages)

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-converted-sticky',
          conversationId: 'conversation-converted-sticky',
        }),
      { wrapper: createQueryWrapper() },
    )

    expect(mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: readyConvertedMessages,
      }),
    )

    const thirdUser = new HumanMessage({ id: 'user-3', content: '바보야' })
    const optimisticConvertedMessages = [
      ...readyConvertedMessages,
      { id: 'user-3', role: 'user', content: [{ type: 'text', text: '바보야' }] },
    ]
    mocks.stream.isLoading = true
    mocks.stream.messages = [...readyMessages, thirdUser]
    mocks.useExternalMessageConverter.mockReturnValue(optimisticConvertedMessages)
    rerender()

    expect(mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: optimisticConvertedMessages,
      }),
    )

    mocks.stream.messages = [firstUser, firstAssistant, secondUser]
    mocks.useExternalMessageConverter.mockReturnValue(readyConvertedMessages.slice(0, 3))
    rerender()

    expect(mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: optimisticConvertedMessages,
      }),
    )
  })

  it('keeps visible user message text when a live stream briefly reports the same user id with empty content', () => {
    const firstUser = new HumanMessage({ id: 'stable-user-1', content: '안녕?' })
    const firstAssistant = new AIMessage({ id: 'stable-assistant-1', content: '안녕하세요!' })
    const secondUser = new HumanMessage({ id: 'stable-user-2', content: '반가워' })
    mocks.stream.messages = [firstUser, firstAssistant, secondUser]
    mocks.useExternalMessageConverter.mockReturnValue([
      { id: 'stable-user-1', role: 'user', content: [{ type: 'text', text: '안녕?' }] },
      {
        id: 'stable-assistant-1',
        role: 'assistant',
        content: [{ type: 'text', text: '안녕하세요!' }],
      },
      { id: 'stable-user-2', role: 'user', content: [{ type: 'text', text: '반가워' }] },
    ])

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-user-text-sticky',
          conversationId: 'conversation-user-text-sticky',
        }),
      { wrapper: createQueryWrapper() },
    )

    const transientBlankSecondUser = new HumanMessage({
      id: 'stable-user-2',
      content: '',
    })
    mocks.stream.isLoading = true
    mocks.stream.messages = [firstUser, firstAssistant, transientBlankSecondUser]
    mocks.useExternalMessageConverter.mockReturnValue([
      { id: 'stable-user-1', role: 'user', content: [{ type: 'text', text: '안녕?' }] },
      {
        id: 'stable-assistant-1',
        role: 'assistant',
        content: [{ type: 'text', text: '안녕하세요!' }],
      },
      { id: 'stable-user-2', role: 'user', content: [{ type: 'text', text: '' }] },
    ])
    rerender()

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
      | { messages: readonly { id?: string; content?: unknown }[] }
      | undefined
    expect(converterOptions?.messages).toEqual([
      expect.objectContaining({ id: 'stable-user-1', content: '안녕?' }),
      expect.objectContaining({ id: 'stable-assistant-1', content: '안녕하세요!' }),
      expect.objectContaining({ id: 'stable-user-2', content: '반가워' }),
    ])
  })

  it('keeps completed middle turns when SDK briefly reports an older prefix plus the newest user turn', () => {
    const firstUser = new HumanMessage({ id: 'middle-user-1', content: '안녕?' })
    const firstAssistant = new AIMessage({ id: 'middle-assistant-1', content: '안녕하세요!' })
    const secondUser = new HumanMessage({ id: 'middle-user-2', content: '반가워' })
    const secondAssistant = new AIMessage({ id: 'middle-assistant-2', content: '반갑습니다!' })
    const thirdUser = new HumanMessage({ id: 'middle-user-3', content: '바보야' })
    const readyMessages = [firstUser, firstAssistant, secondUser, secondAssistant]
    mocks.useExternalMessageConverter.mockImplementation(
      (options: { messages: readonly unknown[] }) =>
        (options.messages as readonly (HumanMessage | AIMessage)[]).map((message) => ({
          id: message.id as string,
          role: message._getType() === 'human' ? 'user' : 'assistant',
          content: [{ type: 'text', text: String(message.content) }],
        })),
    )
    mocks.stream.messages = readyMessages

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-middle-turn-sticky',
          conversationId: 'conversation-middle-turn-sticky',
        }),
      { wrapper: createQueryWrapper() },
    )

    mocks.stream.isLoading = true
    mocks.stream.messages = [...readyMessages, thirdUser]
    rerender()

    expect(mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: [
          expect.objectContaining({ id: 'middle-user-1' }),
          expect.objectContaining({ id: 'middle-assistant-1' }),
          expect.objectContaining({ id: 'middle-user-2' }),
          expect.objectContaining({ id: 'middle-assistant-2' }),
          expect.objectContaining({ id: 'middle-user-3' }),
        ],
      }),
    )

    mocks.stream.messages = [firstUser, firstAssistant, thirdUser]
    rerender()

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
      | { messages: readonly { id?: string }[] }
      | undefined
    expect(converterOptions?.messages.map((message) => message.id)).toEqual([
      'middle-user-1',
      'middle-assistant-1',
      'middle-user-2',
      'middle-assistant-2',
      'middle-user-3',
    ])
    expect(mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: [
          expect.objectContaining({ id: 'middle-user-1' }),
          expect.objectContaining({ id: 'middle-assistant-1' }),
          expect.objectContaining({ id: 'middle-user-2' }),
          expect.objectContaining({ id: 'middle-assistant-2' }),
          expect.objectContaining({ id: 'middle-user-3' }),
        ],
      }),
    )
  })

  it('keeps an optimistic converted user turn when server hydration briefly replaces source with an older prefix', async () => {
    const firstUser = new HumanMessage({ id: 'hydration-user-1', content: '안녕?' })
    const firstAssistant = new AIMessage({ id: 'hydration-assistant-1', content: '안녕하세요!' })
    const secondUser = new HumanMessage({ id: 'hydration-user-2', content: '반가워' })
    const secondAssistant = new AIMessage({ id: 'hydration-assistant-2', content: '반갑습니다!' })
    const thirdUser = new HumanMessage({ id: 'hydration-user-3', content: '바보야' })
    const readyMessages = [firstUser, firstAssistant, secondUser, secondAssistant]
    const readyConvertedMessages = [
      { id: 'hydration-user-1', role: 'user', content: [{ type: 'text', text: '안녕?' }] },
      {
        id: 'hydration-assistant-1',
        role: 'assistant',
        content: [{ type: 'text', text: '안녕하세요!' }],
      },
      { id: 'hydration-user-2', role: 'user', content: [{ type: 'text', text: '반가워' }] },
      {
        id: 'hydration-assistant-2',
        role: 'assistant',
        content: [{ type: 'text', text: '반갑습니다!' }],
      },
    ]
    const optimisticConvertedMessages = [
      ...readyConvertedMessages,
      { id: 'hydration-user-3', role: 'user', content: [{ type: 'text', text: '바보야' }] },
    ]
    mocks.stream.messages = readyMessages
    mocks.useExternalMessageConverter.mockReturnValue(readyConvertedMessages)

    const { rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hydration-sticky',
          conversationId: 'conversation-hydration-sticky',
        }),
      { wrapper: createQueryWrapper() },
    )

    mocks.stream.isLoading = true
    mocks.stream.messages = [...readyMessages, thirdUser]
    mocks.useExternalMessageConverter.mockReturnValue(optimisticConvertedMessages)
    rerender()

    expect(mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        messages: optimisticConvertedMessages,
      }),
    )

    mocks.useExternalMessageConverter.mockReturnValue(readyConvertedMessages.slice(0, 3))
    mocks.apiFetch.mockResolvedValueOnce({
      values: {
        messages: [
          { type: 'human', id: 'hydration-user-1', content: '안녕?' },
          { type: 'ai', id: 'hydration-assistant-1', content: '안녕하세요!' },
          { type: 'human', id: 'hydration-user-2', content: '반가워' },
        ],
      },
    })

    await act(async () => {
      dispatchMoldyBranchSwitched({
        conversationId: 'conversation-hydration-sticky',
        checkpointId: 'ck-stale-prefix',
      })
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]).toEqual(
        expect.objectContaining({
          messages: optimisticConvertedMessages,
        }),
      )
    })
  })

  it('routes assistant-ui cancel through the LangGraph stream stop contract', async () => {
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-cancel',
          conversationId: 'conversation-cancel',
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as {
      onCancel: () => Promise<void>
    }

    await runtimeOptions.onCancel()

    expect(mocks.stream.stop).toHaveBeenCalled()
    expect(mocks.stream.disconnect).not.toHaveBeenCalled()
  })

  it('adds a local canceled notice after assistant-ui stops a v3 run', async () => {
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-cancel',
          conversationId: 'conversation-cancel',
        }),
      { wrapper: createQueryWrapper() },
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as {
      onCancel: () => Promise<void>
    }

    await act(async () => {
      await runtimeOptions.onCancel()
    })

    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { messages: readonly unknown[] }
        | undefined
      expect(converterOptions?.messages).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: 'moldy-canceled-local-conversation-cancel',
            content: 'canceled',
          }),
        ]),
      )
    })
  })

  it('adds a stale notice from hydrated v3 thread state', async () => {
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-stale',
          conversationId: 'conversation-stale',
        }),
      { wrapper: createQueryWrapper() },
    )

    const transportOptions = mocks.createMoldyAgentTransport.mock.calls.at(-1)?.[2] as
      | { onState?: (state: unknown) => void }
      | undefined
    act(() => {
      transportOptions?.onState?.({
        metadata: { latest_run: { id: 'run-stale', status: 'stale' } },
      })
    })

    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { messages: readonly unknown[]; isRunning: boolean }
        | undefined
      expect(converterOptions?.isRunning).toBe(false)
      expect(converterOptions?.messages).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: 'moldy-stale-run-stale',
            content: 'stale',
          }),
        ]),
      )
    })
  })

  it('merges branch metadata from hydrated v3 state into stream messages', async () => {
    mocks.stream.messages = [new AIMessage({ id: 'assistant-branch', content: 'answer' })]
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-branch',
          conversationId: 'conversation-branch',
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
              id: 'assistant-branch',
              additional_kwargs: {
                metadata: {
                  branches: ['assistant-old', 'assistant-branch'],
                  siblingCheckpointIds: ['ck-old', 'ck-new'],
                  activeBranchId: 'assistant-branch',
                  branchIndex: 1,
                  branchTotal: 2,
                },
              },
            },
          ],
        },
      })
    })

    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { messages: readonly { additional_kwargs?: { metadata?: unknown } }[] }
        | undefined
      expect(converterOptions?.messages[0]?.additional_kwargs?.metadata).toEqual(
        expect.objectContaining({
          branches: ['assistant-old', 'assistant-branch'],
          siblingCheckpointIds: ['ck-old', 'ck-new'],
          branchIndex: 1,
          branchTotal: 2,
        }),
      )
    })
  })

  it('projects pending ask_user interrupts from hydrated thread tasks into the transcript', async () => {
    mocks.stream.messages = [
      new HumanMessage({
        id: 'human-ask-user',
        content: '사과, 배, 포도 중에 하나 선택하는 ask user 해줘',
      }),
      new AIMessage({ id: 'assistant-preface', content: '네, 골라봐요!' }),
    ]
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-ask-user-state',
          conversationId: 'conversation-ask-user-state',
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
              type: 'human',
              id: 'human-ask-user',
              content: '사과, 배, 포도 중에 하나 선택하는 ask user 해줘',
            },
            { type: 'ai', id: 'assistant-preface', content: '네, 골라봐요!' },
          ],
        },
        tasks: [
          {
            id: 'run-ask-user',
            name: 'interrupted',
            interrupts: [
              {
                id: 'intr-ask-user',
                ns: [],
                value: {
                  action_requests: [
                    {
                      name: 'ask_user',
                      args: {
                        mode: 'option_list',
                        title: '입력이 필요합니다',
                        question: '어떤 과일이 좋아요?',
                        options: [
                          { id: 'apple', label: '🍎 사과' },
                          { id: 'pear', label: '🍐 배' },
                          { id: 'grape', label: '🍇 포도' },
                        ],
                      },
                    },
                  ],
                  review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
                },
              },
            ],
          },
        ],
      })
    })

    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { messages: readonly { id?: string; tool_calls?: readonly { name?: string }[] }[] }
        | undefined
      expect(converterOptions?.messages).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: 'moldy-hitl:intr-ask-user',
            tool_calls: [
              expect.objectContaining({
                name: 'ask_user',
              }),
            ],
          }),
        ]),
      )
    })
  })

  it('hydrates pending ask_user interrupts from persisted thread state on mount', async () => {
    const conversationId = '11111111-1111-4111-8111-111111111111'
    mocks.stream.messages = [
      new HumanMessage({
        id: 'human-ask-user-mount',
        content: '사과, 배, 포도 중에 하나 선택하는 ask user 해줘',
      }),
      new AIMessage({ id: 'assistant-preface-mount', content: '네, 골라봐요!' }),
    ]
    mocks.apiFetch.mockResolvedValueOnce({
      values: {
        messages: [
          {
            type: 'human',
            id: 'human-ask-user-mount',
            content: '사과, 배, 포도 중에 하나 선택하는 ask user 해줘',
          },
          { type: 'ai', id: 'assistant-preface-mount', content: '네, 골라봐요!' },
        ],
      },
      tasks: [
        {
          id: 'run-ask-user-mount',
          name: 'interrupted',
          interrupts: [
            {
              id: 'intr-ask-user-mount',
              ns: [],
              value: {
                action_requests: [
                  {
                    name: 'ask_user',
                    args: {
                      mode: 'option_list',
                      title: '입력이 필요합니다',
                      question: '어떤 과일이 좋아요?',
                      options: [
                        { id: 'apple', label: '🍎 사과' },
                        { id: 'pear', label: '🍐 배' },
                        { id: 'grape', label: '🍇 포도' },
                      ],
                    },
                  },
                ],
                review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
              },
            },
          ],
        },
      ],
    })

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-ask-user-state',
          conversationId,
        }),
      { wrapper: createQueryWrapper() },
    )

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        `/api/conversations/${conversationId}/langgraph/threads/${conversationId}/state`,
      )
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { messages: readonly { id?: string; tool_calls?: readonly { name?: string }[] }[] }
        | undefined
      expect(converterOptions?.messages).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: 'moldy-hitl:intr-ask-user-mount',
            tool_calls: [
              expect.objectContaining({
                name: 'ask_user',
              }),
            ],
          }),
        ]),
      )
    })
  })

  it('hydrates a persisted ask_user tool call from interrupted thread state without duplicating it', async () => {
    const conversationId = '22222222-2222-4222-8222-222222222222'
    const askUserArgs = {
      mode: 'option_list',
      title: '입력이 필요합니다',
      question: '어떤 과일이 좋아요?',
      options: [
        { id: 'apple', label: '🍎 사과' },
        { id: 'pear', label: '🍐 배' },
        { id: 'grape', label: '🍇 포도' },
      ],
    }
    mocks.stream.messages = []
    mocks.apiFetch.mockResolvedValueOnce({
      values: {
        messages: [
          {
            type: 'human',
            id: 'human-ask-user-persisted',
            content: '사과, 배, 포도 중에 하나 선택하는 ask user 해줘',
          },
          {
            type: 'ai',
            id: 'assistant-ask-user-persisted',
            content: [
              { type: 'text', text: '네, 골라봐요!', index: 0 },
              {
                type: 'tool_call',
                id: 'call_e2e_ask_user_fruit',
                name: 'ask_user',
                args: askUserArgs,
              },
            ],
            tool_calls: [
              {
                id: 'call_e2e_ask_user_fruit',
                name: 'ask_user',
                args: askUserArgs,
                type: 'tool_call',
              },
            ],
          },
        ],
      },
      tasks: [
        {
          id: 'run-ask-user-persisted',
          name: 'interrupted',
          interrupts: [
            {
              id: 'intr-ask-user-persisted',
              ns: [],
              value: {
                action_requests: [{ name: 'ask_user', args: askUserArgs }],
                review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
              },
            },
          ],
        },
      ],
    })

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-ask-user-state',
          conversationId,
        }),
      { wrapper: createQueryWrapper() },
    )

    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | {
            messages: readonly {
              id?: string
              tool_calls?: readonly {
                id?: string
                name?: string
                args?: Record<string, unknown>
              }[]
            }[]
          }
        | undefined
      const messages = converterOptions?.messages ?? []
      const askUserToolCalls = messages.flatMap((message) =>
        (message.tool_calls ?? []).filter((toolCall) => toolCall.name === 'ask_user'),
      )
      const assistantMessage = messages.find(
        (message) => message.id === 'assistant-ask-user-persisted',
      )

      expect(askUserToolCalls).toHaveLength(1)
      expect(assistantMessage).toEqual(
        expect.objectContaining({
          status: { type: 'requires-action', reason: 'tool-calls' },
        }),
      )
      expect(askUserToolCalls[0]).toEqual(
        expect.objectContaining({
          id: 'call_e2e_ask_user_fruit',
          args: expect.objectContaining({
            approval_id: 'call_e2e_ask_user_fruit',
            hitl_interrupt_id: 'intr-ask-user-persisted',
            hitl_action_index: 0,
            hitl_total_actions: 1,
          }),
        }),
      )
      expect(messages.some((message) => message.id === 'moldy-hitl:intr-ask-user-persisted')).toBe(
        false,
      )
    })
  })

  it('hydrates stable server ids onto idless live messages before edit actions', async () => {
    mocks.stream.messages = [new HumanMessage('Original user message')]
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-idless',
          conversationId: 'conversation-idless',
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
              type: 'human',
              id: 'stable-user-id',
              content: 'Original user message',
              additional_kwargs: {
                metadata: {
                  checkpoint_id: 'ck-after-user',
                },
              },
            },
          ],
        },
      })
    })

    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { messages: readonly { id?: string; additional_kwargs?: unknown }[] }
        | undefined
      expect(converterOptions?.messages[0]).toEqual(
        expect.objectContaining({
          id: 'stable-user-id',
          additional_kwargs: expect.objectContaining({
            metadata: expect.objectContaining({
              checkpoint_id: 'ck-after-user',
            }),
          }),
        }),
      )
    })
  })

  it('does not render a lone blank assistant placeholder before the optimistic user message arrives', () => {
    mocks.stream.isLoading = true
    mocks.stream.messages = [new AIMessage({ id: 'stream-placeholder', content: '' })]

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-first-frame',
          conversationId: 'conversation-first-frame',
        }),
      { wrapper: createQueryWrapper() },
    )

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
      | { messages: readonly unknown[] }
      | undefined
    expect(converterOptions?.messages).toEqual([])
  })

  it('passes running state without mutating assistant message metadata', () => {
    mocks.stream.isLoading = true
    mocks.stream.messages = [
      new HumanMessage({ id: 'first-user', content: 'first prompt' }),
      new AIMessage({ id: 'first-assistant', content: 'first answer' }),
      new HumanMessage({ id: 'second-user', content: 'second prompt' }),
      new AIMessage({ id: 'second-assistant', content: 'partial second answer' }),
    ]

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-streaming-current-turn',
          conversationId: 'conversation-streaming-current-turn',
        }),
      { wrapper: createQueryWrapper() },
    )

    const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
      | {
          readonly isRunning?: boolean
          readonly messages: readonly {
            readonly id?: string
            readonly additional_kwargs?: unknown
          }[]
        }
      | undefined
    expect(converterOptions?.isRunning).toBe(true)
    expect(converterOptions?.messages[1]?.additional_kwargs).not.toEqual(
      expect.objectContaining({
        metadata: expect.objectContaining({ isStreamingMessage: true }),
      }),
    )
    expect(converterOptions?.messages[3]).toBe(mocks.stream.messages[3])
    expect(converterOptions?.messages[3]?.additional_kwargs).not.toEqual(
      expect.objectContaining({
        metadata: expect.objectContaining({ isStreamingMessage: true }),
      }),
    )
  })

  it('hydrates branch-selected v3 messages after the shared branch picker switches checkpoint', async () => {
    mocks.stream.messages = [new AIMessage({ id: 'assistant-new', content: 'new answer' })]
    mocks.apiFetch.mockResolvedValueOnce({
      values: {
        messages: [
          { type: 'human', id: 'human-old', content: 'old question' },
          {
            type: 'ai',
            id: 'assistant-old',
            content: 'old answer',
            additional_kwargs: {
              metadata: {
                branchIndex: 0,
                branchTotal: 2,
                siblingCheckpointIds: ['ck-old', 'ck-new'],
              },
            },
          },
        ],
      },
    })

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-branch-switch',
          conversationId: 'conversation-branch-switch',
        }),
      { wrapper: createQueryWrapper() },
    )

    await act(async () => {
      dispatchMoldyBranchSwitched({
        conversationId: 'conversation-branch-switch',
        checkpointId: 'ck-old',
      })
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/conversations/conversation-branch-switch/langgraph/threads/conversation-branch-switch/state',
      )
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { messages: readonly { id?: string; content?: unknown; additional_kwargs?: unknown }[] }
        | undefined
      expect(converterOptions?.messages).toEqual([
        expect.objectContaining({ id: 'human-old', content: 'old question' }),
        expect.objectContaining({
          id: 'assistant-old',
          content: 'old answer',
          additional_kwargs: expect.objectContaining({
            metadata: expect.objectContaining({
              branchIndex: 0,
              branchTotal: 2,
              siblingCheckpointIds: ['ck-old', 'ck-new'],
            }),
          }),
        }),
      ])
    })
  })

  it('does not render branch-selected server messages after the conversation changes', async () => {
    mocks.stream.messages = [new AIMessage({ id: 'assistant-a', content: 'live answer A' })]
    mocks.apiFetch.mockResolvedValueOnce({
      values: {
        messages: [{ type: 'ai', id: 'assistant-old-branch', content: 'old branch answer' }],
      },
    })
    const { rerender } = renderHook(
      ({ conversationId }: { conversationId: string }) =>
        useMoldyLangGraphStream({
          agentId: 'agent-branch-reset',
          conversationId,
        }),
      {
        initialProps: { conversationId: 'conversation-a' },
        wrapper: createQueryWrapper(),
      },
    )

    await act(async () => {
      dispatchMoldyBranchSwitched({
        conversationId: 'conversation-a',
        checkpointId: 'ck-old',
      })
      await Promise.resolve()
    })

    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { messages: readonly { id?: string; content?: unknown }[] }
        | undefined
      expect(converterOptions?.messages).toEqual([
        expect.objectContaining({ id: 'assistant-old-branch', content: 'old branch answer' }),
      ])
    })

    mocks.stream.messages = [new AIMessage({ id: 'assistant-b', content: 'live answer B' })]
    rerender({ conversationId: 'conversation-b' })

    await waitFor(() => {
      const converterOptions = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { messages: readonly { id?: string; content?: unknown }[] }
        | undefined
      expect(converterOptions?.messages).toEqual([
        expect.objectContaining({ id: 'assistant-b', content: 'live answer B' }),
      ])
      expect(JSON.stringify(converterOptions?.messages)).not.toContain('old branch answer')
    })
  })

  it('projects LangGraph HITL interrupts into assistant-ui tool call messages', () => {
    mocks.stream.interrupts = [
      {
        id: 'intr-1',
        value: {
          action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
          review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve', 'reject'] }],
        },
      },
    ]

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hitl',
          conversationId: 'conversation-hitl',
        }),
      { wrapper: createQueryWrapper() },
    )

    const calls = mocks.useExternalMessageConverter.mock.calls
    const converterOptions = calls[calls.length - 1]?.[0]

    expect(converterOptions).toBeDefined()
    expect(converterOptions.messages).toHaveLength(1)
    expect(converterOptions.messages[0]).toEqual(
      expect.objectContaining({
        tool_calls: [
          expect.objectContaining({
            id: 'intr-1:0',
            name: 'request_approval',
          }),
        ],
      }),
    )
  })

  it('resumes a targeted LangGraph interrupt with standard decisions', async () => {
    const { result } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hitl',
          conversationId: 'conversation-hitl',
        }),
      { wrapper: createQueryWrapper() },
    )

    await result.current.onResumeDecisions([{ type: 'approve' }], '승인', 'intr-1')

    expect(mocks.stream.respond).toHaveBeenCalledWith(
      { decisions: [{ type: 'approve' }] },
      { interruptId: 'intr-1' },
    )
    expect(mocks.thread.subscribe).toHaveBeenCalledWith('lifecycle', {
      namespaces: [[]],
      depth: 0,
    })
    expect(mocks.lifecycleSubscription.unsubscribe).toHaveBeenCalled()
  })

  it('projects and resumes nested thread interrupts with their namespace', async () => {
    mocks.thread.interrupts = [
      {
        interruptId: 'intr-subgraph',
        namespace: ['tools:call-1'],
        payload: {
          action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
          review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve'] }],
        },
      },
    ]
    const { result } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hitl',
          conversationId: 'conversation-hitl',
        }),
      { wrapper: createQueryWrapper() },
    )

    const calls = mocks.useExternalMessageConverter.mock.calls
    const converterOptions = calls[calls.length - 1]?.[0]

    expect(converterOptions.messages).toEqual([
      expect.objectContaining({
        tool_calls: [
          expect.objectContaining({
            id: 'intr-subgraph:0',
            name: 'request_approval',
          }),
        ],
      }),
    ])

    await result.current.onResumeDecisions([{ type: 'approve' }], '승인', 'intr-subgraph')

    expect(mocks.stream.respond).toHaveBeenCalledWith(
      { decisions: [{ type: 'approve' }] },
      { interruptId: 'intr-subgraph', namespace: ['tools:call-1'] },
    )
  })

  it('keeps resolved HITL approval results visible after resume', async () => {
    mocks.stream.interrupts = [
      {
        id: 'intr-1',
        value: {
          action_requests: [{ name: 'execute_in_skill', args: { command: 'make-docx' } }],
          review_configs: [
            { action_name: 'execute_in_skill', allowed_decisions: ['approve', 'reject'] },
          ],
        },
      },
    ]
    const { result } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hitl',
          conversationId: 'conversation-hitl',
        }),
      { wrapper: createQueryWrapper() },
    )

    await act(async () => {
      await result.current.registerDecision(0, { type: 'reject' }, 'rejected', 'intr-1')
    })

    expect(mocks.stream.respond).toHaveBeenCalledWith(
      { decisions: [{ type: 'reject' }] },
      { interruptId: 'intr-1' },
    )

    await waitFor(() => {
      const calls = mocks.useExternalMessageConverter.mock.calls
      const converterOptions = calls[calls.length - 1]?.[0] as
        | { messages: readonly unknown[] }
        | undefined
      expect(converterOptions?.messages).toEqual([
        expect.objectContaining({
          tool_calls: [
            expect.objectContaining({
              id: 'intr-1:0',
              name: 'request_approval',
            }),
          ],
        }),
        expect.objectContaining({
          tool_call_id: 'intr-1:0',
          content: '{"decision":"rejected"}',
        }),
      ])
    })
  })

  it('batches multi-action decisions for the same interrupt before resume', async () => {
    mocks.stream.interrupts = [
      {
        id: 'intr-multi',
        value: {
          action_requests: [
            { name: 'ask_user', args: { question: '계속할까요?' } },
            { name: 'send_email', args: { to: 'team@example.com' } },
          ],
          review_configs: [
            { action_name: 'ask_user', allowed_decisions: ['respond'] },
            { action_name: 'send_email', allowed_decisions: ['approve', 'reject'] },
          ],
        },
      },
    ]
    const { result } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hitl',
          conversationId: 'conversation-hitl',
        }),
      { wrapper: createQueryWrapper() },
    )

    await result.current.registerDecision(
      1,
      { type: 'reject', message: '아니요' },
      '거부',
      'intr-multi',
    )
    expect(mocks.stream.respond).not.toHaveBeenCalled()

    await result.current.registerDecision(0, { type: 'respond', message: '네' }, '네', 'intr-multi')

    expect(mocks.stream.respond).toHaveBeenCalledWith(
      {
        decisions: [
          { type: 'respond', message: '네' },
          { type: 'reject', message: '아니요' },
        ],
      },
      { interruptId: 'intr-multi' },
    )
  })

  it('uses the latest interrupt namespace when a multi-action coordinator resumes', async () => {
    const payload = {
      action_requests: [
        { name: 'ask_user', args: { question: '계속할까요?' } },
        { name: 'send_email', args: { to: 'team@example.com' } },
      ],
      review_configs: [
        { action_name: 'ask_user', allowed_decisions: ['respond'] },
        { action_name: 'send_email', allowed_decisions: ['approve', 'reject'] },
      ],
    }
    mocks.stream.interrupts = [{ id: 'intr-multi', value: payload }]
    const { result, rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hitl',
          conversationId: 'conversation-hitl',
        }),
      { wrapper: createQueryWrapper() },
    )

    await result.current.registerDecision(
      1,
      { type: 'reject', message: '아니요' },
      '거부',
      'intr-multi',
    )
    expect(mocks.stream.respond).not.toHaveBeenCalled()

    mocks.stream.interrupts = []
    mocks.thread.interrupts = [
      {
        interruptId: 'intr-multi',
        namespace: ['tools:latest'],
        payload,
      },
    ]
    rerender()
    await result.current.registerDecision(0, { type: 'respond', message: '네' }, '네', 'intr-multi')

    expect(mocks.stream.respond).toHaveBeenCalledWith(
      {
        decisions: [
          { type: 'respond', message: '네' },
          { type: 'reject', message: '아니요' },
        ],
      },
      { interruptId: 'intr-multi', namespace: ['tools:latest'] },
    )
  })

  it('resumes concurrent single-action interrupts with respondAll after every pending interrupt has a decision', async () => {
    mocks.thread.interrupts = [
      {
        interruptId: 'intr-a',
        namespace: ['tools:call-a'],
        payload: {
          action_requests: [{ name: 'send_email', args: { to: 'a@example.com' } }],
          review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve', 'reject'] }],
        },
      },
      {
        interruptId: 'intr-b',
        namespace: ['tools:call-b'],
        payload: {
          action_requests: [{ name: 'send_email', args: { to: 'b@example.com' } }],
          review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve', 'reject'] }],
        },
      },
    ]
    const { result } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hitl',
          conversationId: 'conversation-hitl',
        }),
      { wrapper: createQueryWrapper() },
    )

    await result.current.registerDecision(0, { type: 'approve' }, '승인', 'intr-a')

    expect(mocks.stream.respond).not.toHaveBeenCalled()
    expect(mocks.stream.respondAll).not.toHaveBeenCalled()

    await result.current.registerDecision(0, { type: 'reject', message: '거부' }, '거부', 'intr-b')

    expect(mocks.stream.respond).not.toHaveBeenCalled()
    expect(mocks.stream.respondAll).toHaveBeenCalledWith({
      'intr-a': { decisions: [{ type: 'approve' }] },
      'intr-b': { decisions: [{ type: 'reject', message: '거부' }] },
    })
  })

  it('flushes a pending concurrent interrupt decision when the other active interrupt disappears', async () => {
    mocks.thread.interrupts = [
      {
        interruptId: 'intr-a',
        namespace: ['tools:call-a'],
        payload: {
          action_requests: [{ name: 'send_email', args: { to: 'a@example.com' } }],
          review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve', 'reject'] }],
        },
      },
      {
        interruptId: 'intr-b',
        namespace: ['tools:call-b'],
        payload: {
          action_requests: [{ name: 'send_email', args: { to: 'b@example.com' } }],
          review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve', 'reject'] }],
        },
      },
    ]
    const { result, rerender } = renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hitl',
          conversationId: 'conversation-hitl',
        }),
      { wrapper: createQueryWrapper() },
    )

    await result.current.registerDecision(0, { type: 'approve' }, '승인', 'intr-a')
    expect(mocks.stream.respond).not.toHaveBeenCalled()
    expect(mocks.stream.respondAll).not.toHaveBeenCalled()

    mocks.thread.interrupts = [mocks.thread.interrupts[0]]
    rerender()

    await waitFor(() => {
      expect(mocks.stream.respond).toHaveBeenCalledWith(
        { decisions: [{ type: 'approve' }] },
        { interruptId: 'intr-a', namespace: ['tools:call-a'] },
      )
    })
    expect(mocks.stream.respondAll).not.toHaveBeenCalled()
  })
})
