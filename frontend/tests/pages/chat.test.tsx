import { render, screen } from '../test-utils'
import ChatPage from '@/app/agents/[agentId]/conversations/[conversationId]/page'
import { mockAgent, mockMessageList } from '../mocks/fixtures'

// Mock scrollIntoView for jsdom
Element.prototype.scrollIntoView = vi.fn()

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

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
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

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgent: (...args: unknown[]) => mockUseAgent(...args),
}))

vi.mock('@/lib/hooks/use-conversations', () => ({
  useMessages: (...args: unknown[]) => mockUseMessages(...args),
  useCreateConversation: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useConversations: () => ({
    data: [],
    isLoading: false,
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

vi.mock('@/components/shared/delete-confirm-dialog', () => ({
  DeleteConfirmDialog: () => null,
}))

const mockStreamChat = vi.fn()

vi.mock('@/lib/sse/stream-chat', () => ({
  streamChat: (...args: unknown[]) => mockStreamChat(...args),
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
    mockUseAgent.mockReturnValue({ data: undefined })
    mockUseMessages.mockReturnValue({
      data: undefined,
      isLoading: false,
    })
    mockStreamChat.mockClear()
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

  it('shows agent name in header when loaded', () => {
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
    // Agent name appears in both conversation list header and chat header
    expect(screen.getAllByText('Test Agent').length).toBeGreaterThanOrEqual(1)
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

    // streamChat should have been called with conversationId and message
    expect(mockStreamChat).toHaveBeenCalledWith('conv-1', 'Test message', expect.any(AbortSignal))
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

  it('handles stream error gracefully and shows toast', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()

    mockStreamChat.mockReturnValue(
      (async function* () {
        yield {
          event: 'error',
          data: { message: 'Something went wrong' },
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
    await user.type(textarea, 'Test')
    const sendButton = screen.getByRole('button', { name: /전송/ })
    await user.click(sendButton)

    expect(mockStreamChat).toHaveBeenCalled()
    expect(mockToastError).toHaveBeenCalledWith('Something went wrong')
  })

  it('shows default error message when error event has no message', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()

    mockStreamChat.mockReturnValue(
      (async function* () {
        yield {
          event: 'error',
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
    await user.type(textarea, 'Test')
    const sendButton = screen.getByRole('button', { name: /전송/ })
    await user.click(sendButton)

    expect(mockStreamChat).toHaveBeenCalled()
    expect(mockToastError).toHaveBeenCalledWith('에이전트 실행 중 오류가 발생했습니다')
  })

  it('shows settings link and new conversation button in conversation list', () => {
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
    // ConversationList has settings link and new conversation button
    const settingsLinks = screen.getAllByRole('link')
    const settingsLink = settingsLinks.find(
      (link) => link.getAttribute('href') === '/agents/agent-1/settings',
    )
    expect(settingsLink).toBeDefined()
    // "새 대화" button should be present in conversation list
    expect(screen.getByRole('button', { name: '새 대화' })).toBeInTheDocument()
  })
})
