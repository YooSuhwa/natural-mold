import { render, screen, waitFor } from '../test-utils'
import ChatPage from '@/app/agents/[agentId]/conversations/[conversationId]/page'
import * as chatRuntime from '@/components/chat/chat-runtime-section'
import * as pinnedSummary from '@/components/chat/pinned-conversation-summary'
import * as readiness from '@/components/readiness/agent-readiness-gate'
import * as runtimeMode from '@/lib/chat/runtime-mode'
import { conversationsApi } from '@/lib/api/conversations'
import * as goalsApi from '@/lib/api/conversation-goals'
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
        data: {
          messages: [],
          active_checkpoint_id: null,
          total_cost: 0,
        },
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

  it('handles stream with tool calls', async () => {
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
