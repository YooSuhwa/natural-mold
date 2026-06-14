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
      (conversationId: string, _agentId: string, options?: MockTransportOptions) => ({
        kind: 'transport',
        conversationId,
        onState: options?.onState,
      }),
    ),
    useStream: vi.fn((options: MockUseStreamOptions) => {
      void options
      return stream
    }),
    useChannel: vi.fn(() => []),
    useChannelEffect: vi.fn(),
    useExternalMessageConverter: vi.fn((options: { messages: readonly unknown[] }) => {
      void options
      return [{ id: 'converted' }]
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
    mocks.stream.submit.mockClear()
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
