import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMoldyLangGraphStream } from '../use-moldy-langgraph-stream'
import type { AttachmentAdapter, CompleteAttachment, PendingAttachment } from '@assistant-ui/react'

interface MockInterrupt {
  id: string
  value: unknown
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
  const thread = { subscribe: vi.fn(async () => lifecycleSubscription) }
  stream.getThread.mockReturnValue(thread)
  return {
    STREAM_CONTROLLER,
    lifecycleSubscription,
    metadataStore,
    stream,
    thread,
    createMoldyAgentTransport: vi.fn((conversationId: string) => ({
      kind: 'transport',
      conversationId,
    })),
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
    conversationRunsCancel: vi.fn(),
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

vi.mock('@/lib/api/conversation-runs', () => ({
  conversationRunsApi: {
    cancel: mocks.conversationRunsCancel,
  },
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
    mocks.conversationRunsCancel.mockClear()
    mocks.thread.subscribe.mockClear()
    mocks.thread.subscribe.mockResolvedValue(mocks.lifecycleSubscription)
    mocks.lifecycleSubscription.unsubscribe.mockClear()
    mocks.metadataStore.getSnapshot.mockReturnValue(new Map())
    mocks.useChannelEffect.mockClear()
    mocks.useExternalMessageConverter.mockClear()
    mocks.useExternalStoreRuntime.mockClear()
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

    expect(mocks.createMoldyAgentTransport).toHaveBeenCalledWith('conversation-1', 'agent-1')
    expect(mocks.useStream).toHaveBeenCalledWith(
      expect.objectContaining({
        transport: { kind: 'transport', conversationId: 'conversation-1' },
        threadId: 'conversation-1',
        onCreated: expect.any(Function),
        onCompleted: expect.any(Function),
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
    expect(mocks.stream.disconnect).toHaveBeenCalled()
  })

  it('routes assistant-ui cancel through the Moldy run cancel API before disconnecting', async () => {
    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-cancel',
          conversationId: 'conversation-cancel',
        }),
      { wrapper: createQueryWrapper() },
    )

    const streamOptions = mocks.useStream.mock.calls.at(-1)?.[0]
    if (!streamOptions) throw new Error('useStream was not called')
    streamOptions.onCreated?.({ runId: 'run-cancel-1' })
    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as {
      onCancel: () => Promise<void>
    }

    await runtimeOptions.onCancel()

    expect(mocks.conversationRunsCancel).toHaveBeenCalledWith('conversation-cancel', 'run-cancel-1')
    expect(mocks.stream.disconnect).toHaveBeenCalled()
    expect(mocks.stream.stop).not.toHaveBeenCalled()
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
})
