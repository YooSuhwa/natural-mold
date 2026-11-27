import { render, screen, waitFor } from '../test-utils'
import ChatPage from '@/app/agents/[agentId]/conversations/[conversationId]/page'
import * as chatRuntime from '@/components/chat/chat-runtime-section'
import * as pinnedSummary from '@/components/chat/pinned-conversation-summary'
import * as readiness from '@/components/readiness/agent-readiness-gate'
import * as runtimeMode from '@/lib/chat/runtime-mode'
import * as jotai from 'jotai'
import { useMoldyAgUiActivityEffects } from '@/lib/ag-ui/activity-effects'
import { sessionTokenUsageAtom, latestTurnUsageAtom } from '@/lib/stores/chat-store'
import { conversationsApi } from '@/lib/api/conversations'
import * as goalsApi from '@/lib/api/conversation-goals'
import { independentJobsApi } from '@/lib/api/independent-chat-jobs'
import { http, HttpResponse } from 'msw'
import { server } from '../setup'
import { mockAgent, mockConversation, mockMessageList } from '../mocks/fixtures'

// Mock scrollIntoView for jsdom
Element.prototype.scrollIntoView = vi.fn()

const mockPush = vi.fn()
const mockReplace = vi.fn()

vi.mock('next/link', () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode
    href: string
    [key: string]: unknown
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}))

// Override: pins useParams/usePathname to the chat route fixture.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
  useParams: () => ({ conversationId: 'conv-1' }),
  usePathname: () => '/agents/agent-1/conversations/conv-1',
}))

// Mock React.use() for params Promise
vi.mock('react', async () => {
  const actual = await vi.importActual('react')
  return {
    ...actual,
    use: (value: unknown) => {
      if (value && typeof value === 'object' && 'agentId' in value && 'conversationId' in value)
        return value
      if (value && typeof value === 'object' && 'agentId' in value) return value
      return (actual as Record<string, unknown>).use(value)
    },
  }
})

const mockUseAgent = vi.fn()
const mockUseMessages = vi.fn()
const mockUseConversationTitle = vi.fn()
const mockInvalidateConversationNavigators = vi.fn()
const mockUpsertConversationNavigatorCache = vi.fn()

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgent: (...args: unknown[]) => mockUseAgent(...args),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ data: undefined }),
}))

vi.mock('@/lib/hooks/use-conversations', () => ({
  conversationKeys: {
    list: (agentId: string) => ['conversations', agentId] as const,
    pages: (agentId: string, params: unknown) =>
      ['agents', agentId, 'conversations', 'page', params] as const,
    messages: (conversationId: string) => ['messages', conversationId] as const,
    files: (conversationId: string | null | undefined) =>
      ['conversations', conversationId ?? 'none', 'files'] as const,
    debugTraces: (conversationId: string) => ['debug-traces', conversationId] as const,
    debugTraceDetail: (conversationId: string, traceId: string) =>
      ['debug-traces', conversationId, traceId] as const,
  },
  conversationDetailQueryOptions: (id: string) => ({
    queryKey: ['conversation', id],
    queryFn: async () => ({ ...mockConversation, id }),
  }),
  upsertConversationNavigatorCache: (...args: unknown[]) =>
    mockUpsertConversationNavigatorCache(...args),
  invalidateConversationNavigators: (...args: unknown[]) =>
    mockInvalidateConversationNavigators(...args),
  // W2-7 follow-up 고스트 — 페이지 테스트에선 제안 fetch를 no-op으로.
  useFollowupSuggestionMutation: () => ({
    mutateAsync: async () => ({ suggestion: null }),
  }),
  useMessages: (...args: unknown[]) => mockUseMessages(...args),
  useFetchMessagesEnvelope: () => vi.fn().mockResolvedValue({ messages: [] }),
  useMessagesEnvelope: (...args: unknown[]) => {
    const result = mockUseMessages(...args)
    if (args[1] === false) {
      return {
        data: undefined,
        isLoading: false,
      }
    }
    return {
      ...result,
      data:
        result.data === undefined
          ? undefined
          : {
              messages: result.data,
              conversation_source: result.source === undefined ? 'ui' : result.source,
              active_run: result.activeRun,
              latest_run: result.latestRun,
              active_checkpoint_id: null,
              total_cost: 0,
            },
    }
  },
  useCreateConversation: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useMarkConversationRead: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useConversations: () => ({
    data: [],
    isLoading: false,
  }),
  useConversationPages: () => ({
    data: { pages: [{ items: [], next_cursor: null, has_more: false }] },
    isLoading: false,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
    isFetchingNextPage: false,
  }),
  useGlobalConversationPages: () => ({
    data: { pages: [{ items: [], next_cursor: null, has_more: false }] },
    isLoading: false,
    isError: false,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
    isFetchingNextPage: false,
  }),
  useConversationDebugTraces: () => ({
    data: { conversation_id: 'conv-1', langfuse_enabled: false, traces: [], fallback_reason: null },
    isLoading: false,
    refetch: vi.fn(),
  }),
  useConversationDebugTraceDetail: () => ({
    data: undefined,
    isLoading: false,
    refetch: vi.fn(),
  }),
  useUpdateConversation: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useDeleteConversation: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-conversation-title', () => ({
  useConversationTitle: (...args: unknown[]) => mockUseConversationTitle(...args),
}))

vi.mock('@/lib/chat/runtime-mode', () => ({
  getChatRuntimeMode: () => 'legacy',
}))

vi.mock('@/components/shared/delete-confirm-dialog', () => ({
  DeleteConfirmDialog: () => null,
}))

const mockStreamChat = vi.fn()
const mockStreamStartConversation = vi.fn()

vi.mock('@/lib/sse/stream-chat', () => ({
  streamChat: (...args: unknown[]) => mockStreamChat(...args),
  streamStartConversation: (...args: unknown[]) => mockStreamStartConversation(...args),
}))

const mockToastError = vi.fn()
vi.mock('sonner', () => ({
  toast: { error: (...args: unknown[]) => mockToastError(...args), success: vi.fn() },
}))

vi.mock('jotai', async () => {
  const actual = await vi.importActual('jotai')
  return {
    ...actual,
    useSetAtom: () => vi.fn(),
    useAtomValue: () => ({ inputTokens: 0, outputTokens: 0, cost: 0 }),
  }
})

describe('ChatPage', () => {
  beforeEach(() => {
    mockUseAgent.mockClear()
    mockUseMessages.mockClear()
    mockUseConversationTitle.mockClear()
    mockInvalidateConversationNavigators.mockClear()
    mockUseAgent.mockReturnValue({ data: undefined })
    mockUseConversationTitle.mockReturnValue('Test Conversation')
    mockUseMessages.mockReturnValue({
      data: [],
      isLoading: false,
    })
    mockStreamChat.mockClear()
    mockStreamStartConversation.mockClear()
    mockPush.mockClear()
    mockReplace.mockClear()
    mockToastError.mockClear()
  })

  it('renders chat input area', () => {
    render(
      <ChatPage
        params={
          {
            agentId: 'agent-1',
            conversationId: 'conv-1',
          } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )
    expect(screen.getByPlaceholderText('메시지 입력...')).toBeInTheDocument()
  })

  it.each([
    ['cold', { data: undefined, isLoading: false }],
    ['loading', { data: undefined, isLoading: true }],
    ['unidentified', { data: [], source: null, isLoading: false }],
    ['failed', { data: [], source: 'ui', isLoading: false, isError: true }],
    ['independent', { data: [], source: 'independent_job', isLoading: false }],
  ])('withholds the composer for a %s existing conversation', (_stage, history) => {
    mockUseMessages.mockReturnValue(history)
    render(
      <ChatPage
        params={
          { agentId: 'agent-1', conversationId: 'conv-1' } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )
    expect(screen.queryByPlaceholderText('메시지 입력...')).toBeNull()
  })

  it('restores cached AG-UI usage and clears stale totals when entering loading or draft routes', async () => {
    const store = jotai.createStore()
    const { useSetAtom } = await vi.importActual<typeof import('jotai')>('jotai')
    vi.spyOn(jotai, 'useSetAtom').mockImplementation(useSetAtom)
    vi.spyOn(runtimeMode, 'getChatRuntimeMode').mockReturnValue('ag_ui')
    vi.spyOn(conversationsApi, 'createDraft').mockReturnValue(new Promise(() => {}))
    const activities = [] as const
    vi.spyOn(chatRuntime, 'ChatRuntimeSection').mockImplementation(function UsageRuntime(props) {
      useMoldyAgUiActivityEffects(props.activeConversationId ?? '', activities, {
        serverMessages: props.messages,
      })
      return <></>
    })
    const breakdown = {
      prompt_tokens: 12,
      completion_tokens: 6,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      estimated_cost: 0.01,
    }
    const totals = { inputTokens: 12, outputTokens: 6, cost: 0.01 }
    const previous = { ...breakdown, prompt_tokens: 50 }
    store.set(latestTurnUsageAtom, previous)
    store.set(sessionTokenUsageAtom, { inputTokens: 50, outputTokens: 9, cost: 0.02 })
    const cached = { data: [{ ...mockMessageList[1], usage: breakdown }], isLoading: false }
    const route = (conversationId: string) => (
      <jotai.Provider store={store}>
        <ChatPage
          params={
            { agentId: 'agent-1', conversationId } as unknown as Promise<{
              agentId: string
              conversationId: string
            }>
          }
        />
      </jotai.Provider>
    )
    mockUseMessages.mockReturnValue(cached)
    const page = render(route('conv-1'))
    try {
      expect(store.get(sessionTokenUsageAtom)).toEqual(totals)
      expect(store.get(latestTurnUsageAtom)).toEqual(breakdown)

      mockUseMessages.mockReturnValue({ data: undefined, isLoading: true })
      page.rerender(route('loading-chat'))
      expect(store.get(sessionTokenUsageAtom)).toEqual({ inputTokens: 0, outputTokens: 0, cost: 0 })
      expect(store.get(latestTurnUsageAtom)).toBeNull()

      mockUseMessages.mockReturnValue(cached)
      page.rerender(route('conv-1'))
      expect(store.get(sessionTokenUsageAtom)).toEqual(totals)
      expect(store.get(latestTurnUsageAtom)).toEqual(breakdown)

      page.rerender(route('new'))
      expect(store.get(sessionTokenUsageAtom)).toEqual({ inputTokens: 0, outputTokens: 0, cost: 0 })
      expect(store.get(latestTurnUsageAtom)).toBeNull()
    } finally {
      page.unmount()
      vi.restoreAllMocks()
    }
  })

  it('renders loading skeletons when messages loading', () => {
    mockUseMessages.mockReturnValue({
      data: undefined,
      isLoading: true,
    })
    const { container } = render(
      <ChatPage
        params={
          {
            agentId: 'agent-1',
            conversationId: 'conv-1',
          } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('renders message list when data loaded', () => {
    mockUseMessages.mockReturnValue({
      data: mockMessageList,
      isLoading: false,
    })
    render(
      <ChatPage
        params={
          {
            agentId: 'agent-1',
            conversationId: 'conv-1',
          } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )
    expect(screen.getByText('Hello, how are you?')).toBeInTheDocument()
  })

  it('shows resolved conversation title in header when loaded', () => {
    mockUseAgent.mockReturnValue({ data: mockAgent })
    render(
      <ChatPage
        params={
          {
            agentId: 'agent-1',
            conversationId: 'conv-1',
          } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )
    expect(screen.getByText('Test Conversation')).toBeInTheDocument()
  })

  it('shows empty conversation prompt when no messages', () => {
    mockUseMessages.mockReturnValue({
      data: [],
      isLoading: false,
    })
    render(
      <ChatPage
        params={
          {
            agentId: 'agent-1',
            conversationId: 'conv-1',
          } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )
    expect(screen.getAllByText('대화를 시작해보세요.').length).toBeGreaterThanOrEqual(1)
  })

  it('calls streamChat when message is sent', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()

    // Mock streamChat to return an async iterable that ends immediately
    mockStreamChat.mockReturnValue(
      (async function* () {
        yield {
          event: 'content_delta',
          data: { content: 'Hello!' },
        }
        yield {
          event: 'message_end',
          data: {},
        }
      })(),
    )

    mockUseMessages.mockReturnValue({ data: [], isLoading: false })

    render(
      <ChatPage
        params={
          {
            agentId: 'agent-1',
            conversationId: 'conv-1',
          } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )

    const textarea = screen.getByPlaceholderText('메시지 입력...')
    await user.type(textarea, 'Test message')
    const sendButton = screen.getByRole('button', { name: /전송/ })
    await user.click(sendButton)

    // streamChat: (conversationId, content, signal, options).
    // P1-7 첨부 도입 후 chat-input이 빈 attachmentIds 배열을 항상 전달.
    expect(mockStreamChat).toHaveBeenCalledWith(
      'conv-1',
      'Test message',
      expect.any(AbortSignal),
      expect.objectContaining({ attachmentIds: expect.any(Array) }),
    )
  })

  it('renders draft conversation without loading server messages', () => {
    mockUseAgent.mockReturnValue({ data: mockAgent })
    mockUseMessages.mockReturnValue({
      data: undefined,
      isLoading: true,
    })

    const { container } = render(
      <ChatPage
        params={
          {
            agentId: 'agent-1',
            conversationId: 'new',
          } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )

    expect(mockUseMessages).toHaveBeenCalledWith('new', false)
    expect(screen.getByPlaceholderText('메시지 입력...')).toBeInTheDocument()
    expect(container.querySelectorAll("[data-slot='skeleton']")).toHaveLength(0)
  })

  it('keeps a fresh draft editable if its bootstrap response has no conversation ID', async () => {
    const mode = vi.spyOn(runtimeMode, 'getChatRuntimeMode').mockReturnValue('langgraph_v3')
    const listJobs = vi.spyOn(independentJobsApi, 'list')
    let release!: () => void
    const pending = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.post('*/api/agents/agent-1/conversations/draft', async () => {
        await pending
        return HttpResponse.json({})
      }),
    )
    const params = { agentId: 'agent-1', conversationId: 'new' }
    const { container, unmount } = render(
      <ChatPage params={params as unknown as Promise<typeof params>} />,
    )
    try {
      expect(container.querySelector('[data-slot="skeleton"]')).toBeInTheDocument()
      expect(listJobs).not.toHaveBeenCalled()
      release()
      expect(await screen.findByPlaceholderText('메시지 입력...')).toBeEnabled()
      expect(listJobs).not.toHaveBeenCalled()
    } finally {
      release()
      unmount()
      mode.mockRestore()
      listJobs.mockRestore()
    }
  })

  it('lists jobs for a server-created draft before it has a run', async () => {
    const mode = vi.spyOn(runtimeMode, 'getChatRuntimeMode').mockReturnValue('langgraph_v3')
    const runtime = vi.spyOn(chatRuntime, 'ChatRuntimeSection').mockReturnValue(<></>)
    const listJobs = vi.spyOn(independentJobsApi, 'list').mockResolvedValue({
      enabled: true,
      parent_job: null,
      jobs: [],
      targets: [],
    })
    server.use(
      http.post('*/api/agents/agent-1/conversations/draft', () =>
        HttpResponse.json({ id: 'created-draft' }, { status: 201 }),
      ),
      http.delete('*/api/conversations/created-draft', () => new HttpResponse(null, { status: 204 })),
    )
    const params = { agentId: 'agent-1', conversationId: 'new' }
    const { unmount } = render(<ChatPage params={params as unknown as Promise<typeof params>} />)
    try {
      await waitFor(() => expect(listJobs).toHaveBeenCalledTimes(1))
      expect(listJobs).toHaveBeenCalledWith('created-draft', null, expect.any(AbortSignal))
    } finally {
      unmount()
      mode.mockRestore()
      runtime.mockRestore()
      listJobs.mockRestore()
    }
  })

  it.each([null, 'latest-run'])('waits for history before listing jobs for %s', async (runId) => {
    const listJobs = vi.spyOn(independentJobsApi, 'list').mockResolvedValue({
      enabled: true,
      parent_job: null,
      jobs: [],
      targets: [],
    })
    const params = { agentId: 'agent-1', conversationId: 'conv-1' }
    const page = () => <ChatPage params={params as unknown as Promise<typeof params>} />
    mockUseMessages.mockReturnValue({ isLoading: true })
    const { rerender, unmount } = render(page())
    try {
      expect(listJobs).not.toHaveBeenCalled()
      mockUseMessages.mockReturnValue({
        data: [],
        latestRun: runId && { id: runId, conversation_id: params.conversationId },
      })
      rerender(page())
      await waitFor(() => expect(listJobs).toHaveBeenCalledTimes(1))
      expect(listJobs).toHaveBeenCalledWith(params.conversationId, runId, expect.any(AbortSignal))
    } finally {
      unmount()
      listJobs.mockRestore()
    }
  })

  it('restores the composer only after the new route has its own history', async () => {
    const listJobs = vi.spyOn(independentJobsApi, 'list').mockResolvedValue({
      enabled: true,
      parent_job: null,
      jobs: [],
      targets: [],
    })
    const route = (conversationId: string) => {
      const params = { agentId: 'agent-1', conversationId }
      return <ChatPage params={params as unknown as Promise<typeof params>} />
    }
    const oldRun = { id: 'child-run', conversation_id: 'child-conversation' }
    mockUseMessages.mockReturnValue({ data: [], source: 'independent_job', activeRun: oldRun })
    const { rerender, unmount } = render(route('child-conversation'))
    try {
      expect(screen.queryByPlaceholderText('메시지 입력...')).not.toBeInTheDocument()
      mockUseMessages.mockReturnValue({
        data: [],
        source: 'ui',
        activeRun: oldRun,
        isPlaceholderData: true,
      })
      rerender(route('ordinary-conversation'))
      expect(screen.queryByPlaceholderText('메시지 입력...')).not.toBeInTheDocument()
      expect(listJobs.mock.calls.some(([id]) => id === 'ordinary-conversation')).toBe(false)
      mockUseMessages.mockReturnValue({
        data: [],
        source: 'ui',
        activeRun: { id: 'ordinary-run', conversation_id: 'ordinary-conversation' },
      })
      rerender(route('ordinary-conversation'))
      expect(await screen.findByPlaceholderText('메시지 입력...')).toBeEnabled()
      await waitFor(() =>
        expect(listJobs).toHaveBeenCalledWith(
          'ordinary-conversation',
          'ordinary-run',
          expect.anything(),
        ),
      )
    } finally {
      unmount()
      listJobs.mockRestore()
    }
  })

  it('starts a conversation from draft and replaces the URL after streaming', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()

    mockUseMessages.mockReturnValue({ data: [], isLoading: false })
    mockStreamStartConversation.mockImplementation(
      async function* (_agentId, _content, _signal, options) {
        options.onConversationId('conv-started')
        yield {
          event: 'message_end',
          data: {},
        }
      },
    )

    render(
      <ChatPage
        params={
          {
            agentId: 'agent-1',
            conversationId: 'new',
          } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )

    await user.type(screen.getByPlaceholderText('메시지 입력...'), 'Draft message')
    await user.click(screen.getByRole('button', { name: /전송/ }))

    expect(mockStreamStartConversation).toHaveBeenCalledWith(
      'agent-1',
      'Draft message',
      expect.any(AbortSignal),
      expect.objectContaining({ attachmentIds: expect.any(Array) }),
    )
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith('/agents/agent-1/conversations/conv-started')
    })
  })

  it('waits for a persisted AG-UI draft after a failed bootstrap', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    const draft = { ...mockConversation, id: 'persisted-draft' }
    vi.spyOn(runtimeMode, 'getChatRuntimeMode').mockReturnValue('ag_ui')
    const runtime = vi.spyOn(chatRuntime, 'ChatRuntimeSection').mockReturnValue(<></>)
    vi.spyOn(conversationsApi, 'delete').mockResolvedValue(undefined)
    const createDraft = vi
      .spyOn(conversationsApi, 'createDraft')
      .mockRejectedValueOnce(new Error('temporarily unavailable'))
      .mockResolvedValueOnce(draft)
    const page = render(
      <ChatPage
        params={
          { agentId: 'agent-1', conversationId: 'new' } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )
    try {
      expect(await screen.findByRole('alert')).toHaveTextContent('새 대화를 준비하지 못했어요.')
      expect(runtime).not.toHaveBeenCalled()
      await user.click(screen.getByRole('button', { name: '다시 시도' }))
      await waitFor(() => expect(runtime).toHaveBeenCalled())
      expect(createDraft).toHaveBeenCalledTimes(2)
      for (const [props] of runtime.mock.calls) {
        expect(props).toMatchObject({
          activeConversationId: draft.id,
          useAgUiRuntime: true,
          useLangGraphRuntime: false,
        })
      }
      expect(mockStreamStartConversation).not.toHaveBeenCalled()
      expect(mockStreamChat).not.toHaveBeenCalled()
    } finally {
      page.unmount()
      await Promise.resolve()
      vi.restoreAllMocks()
    }
  })

  it.each(['ag_ui', 'langgraph_v3'] as const)(
    'opens persisted side chat with its own ID using the %s transport',
    async (mode) => {
      const { default: userEvent } = await import('@testing-library/user-event')
      const user = userEvent.setup()
      const parentId = 'conv-main'
      const sideId = 'conv-side'
      vi.spyOn(runtimeMode, 'getChatRuntimeMode').mockReturnValue(mode)
      const runtime = vi.spyOn(chatRuntime, 'ChatRuntimeSection').mockReturnValue(<></>)
      const createSide = vi
        .spyOn(conversationsApi, 'createSideChat')
        .mockResolvedValue({ ...mockConversation, id: sideId })
      const page = render(
        <ChatPage
          params={
            { agentId: 'agent-1', conversationId: parentId } as unknown as Promise<{
              agentId: string
              conversationId: string
            }>
          }
        />,
      )
      try {
        await user.click(screen.getByRole('button', { name: '사이드 채팅' }))
        await waitFor(() => {
          const props = runtime.mock.calls.map(([call]) => call)
          for (const id of [parentId, sideId]) {
            expect(props).toContainEqual(
              expect.objectContaining({
                activeConversationId: id,
                useAgUiRuntime: mode === 'ag_ui',
                useLangGraphRuntime: mode === 'langgraph_v3',
              }),
            )
          }
        })
        expect(createSide).toHaveBeenCalledTimes(1)
        expect(createSide.mock.calls[0][0]).toBe(parentId)
        expect(mockStreamStartConversation).not.toHaveBeenCalled()
        expect(mockStreamChat).not.toHaveBeenCalled()
      } finally {
        page.unmount()
        vi.restoreAllMocks()
      }
    },
  )

  it('promotes a draft goal into the conversation navigator before its first message', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    const initialPath = window.location.pathname
    const conversationId = 'draft-goal'
    const goalTitle = '첫 보고서'
    const goal = {
      id: 'goal-1',
      title: goalTitle,
      criteria: [{ kind: 'nonempty_file' as const, logical_path: 'report.csv' }],
      status: 'active' as const,
      version: 1,
      revision: 1,
      auto_followups: 0,
      last_evidence: null,
      blocked_reason: null,
    }
    const envelope = { enabled: true, goal }
    const deleted = vi.spyOn(conversationsApi, 'delete').mockResolvedValue(undefined)
    vi.spyOn(runtimeMode, 'getChatRuntimeMode').mockReturnValue('langgraph_v3')
    vi.spyOn(chatRuntime, 'ChatRuntimeSection').mockReturnValue(<></>)
    vi.spyOn(pinnedSummary, 'PinnedConversationSummary').mockReturnValue(null)
    vi.spyOn(readiness, 'AgentReadinessGate').mockReturnValue(<></>)
    vi.spyOn(conversationsApi, 'createDraft').mockResolvedValue({
      ...mockConversation,
      id: conversationId,
    })
    const getGoal = vi
      .spyOn(goalsApi, 'getConversationGoal')
      .mockResolvedValue({ enabled: true, goal: null })
    let accept!: (response: typeof envelope) => void
    const pendingSave = new Promise<typeof envelope>((resolve) => {
      accept = resolve
    })
    const saveGoal = vi.spyOn(goalsApi, 'putConversationGoal').mockReturnValue(pendingSave)
    mockUseAgent.mockReturnValue({ data: mockAgent })
    mockUseMessages.mockReturnValue({ data: [], isLoading: false })
    mockUpsertConversationNavigatorCache.mockClear()
    window.history.replaceState(null, '', '/agents/agent-1/conversations/new')
    let unmount: (() => void) | undefined
    try {
      const page = render(
        <ChatPage
          params={
            { agentId: 'agent-1', conversationId: 'new' } as unknown as Promise<{
              agentId: string
              conversationId: string
            }>
          }
        />,
      )
      unmount = page.unmount
      await user.click(await screen.findByRole('button', { name: '목표 만들기' }))
      await user.type(screen.getByRole('textbox', { name: '목표 이름' }), goalTitle)
      await user.type(screen.getByRole('textbox', { name: '결과 파일 경로' }), 'report.csv')
      await user.click(screen.getByRole('button', { name: '저장' }))
      expect(saveGoal).toHaveBeenCalledWith(conversationId, {
        expected_revision: 0,
        title: goalTitle,
        criteria: goal.criteria,
      })
      expect(window.location.pathname).toBe('/agents/agent-1/conversations/new')
      expect(mockUpsertConversationNavigatorCache).not.toHaveBeenCalled()
      getGoal.mockResolvedValue(envelope)
      accept(envelope)
      await waitFor(() => {
        expect(window.location.pathname).toBe(`/agents/agent-1/conversations/${conversationId}`)
        expect(mockUpsertConversationNavigatorCache).toHaveBeenCalledWith(
          expect.anything(),
          expect.objectContaining({ id: conversationId }),
          expect.objectContaining({ id: mockAgent.id }),
        )
      })
      expect(await screen.findByText(goalTitle)).toBeVisible()
      expect(mockStreamStartConversation).not.toHaveBeenCalled()
      expect(mockStreamChat).not.toHaveBeenCalled()
      unmount()
      unmount = undefined
      await Promise.resolve()
      expect(deleted).not.toHaveBeenCalled()
    } finally {
      unmount?.()
      await Promise.resolve()
      window.history.replaceState(null, '', initialPath)
      vi.restoreAllMocks()
    }
  })

  it('displays the remaining reply when a legacy tool event lacks its public name', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()

    mockStreamChat.mockReturnValue(
      (async function* () {
        yield {
          event: 'tool_call_start',
          data: { name: 'web_search', args: { query: 'test' } },
        }
        yield {
          event: 'tool_call_result',
          data: { result: 'search results' },
        }
        yield {
          event: 'content_delta',
          data: { delta: 'Based on search...' },
        }
        yield {
          event: 'message_end',
          data: {},
        }
      })(),
    )

    mockUseMessages.mockReturnValue({ data: [], isLoading: false })

    render(
      <ChatPage
        params={
          {
            agentId: 'agent-1',
            conversationId: 'conv-1',
          } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )

    const textarea = screen.getByPlaceholderText('메시지 입력...')
    await user.type(textarea, 'Search for something')
    const sendButton = screen.getByRole('button', { name: /전송/ })
    await user.click(sendButton)

    expect(mockStreamChat).toHaveBeenCalled()
    expect(await screen.findByText('Based on search...')).toBeInTheDocument()
  })

  // streamChat 에러 처리는 useChatRuntime 내부로 이동 (M? assistant-ui 통합).
  // 페이지 외부에서 mock한 streamChat 결과가 toast.error로 직접 변환되지 않으므로
  // 단위 테스트로 검증 불가. e2e/smoke 또는 manual QA로 대체.
  it('removes the chat-local conversation list and keeps compact agent context', () => {
    mockUseAgent.mockReturnValue({ data: mockAgent })
    render(
      <ChatPage
        params={
          {
            agentId: 'agent-1',
            conversationId: 'conv-1',
          } as unknown as Promise<{
            agentId: string
            conversationId: string
          }>
        }
      />,
    )
    expect(screen.queryByText('대화')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '에이전트 정보' })).toBeInTheDocument()
  })
})
