import { render, screen, waitFor, userEvent } from '../../test-utils'
import { ConversationList } from '@/components/chat/conversation-list'

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

const mockPush = vi.fn()

// Override: needs named ``mockPush`` spy for push-assertion tests.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn() }),
  useParams: () => ({ conversationId: 'conv-1' }),
  usePathname: () => '/agents/agent-1/conversations/conv-1',
}))

const mockConversations = [
  {
    id: 'conv-1',
    agent_id: 'agent-1',
    title: 'Test Conversation',
    is_pinned: false,
    unread_count: 0,
    last_read_at: null,
    last_unread_at: null,
    last_activity_source: 'user',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'conv-2',
    agent_id: 'agent-1',
    title: 'Second Conversation',
    is_pinned: false,
    unread_count: 2,
    last_read_at: null,
    last_unread_at: '2026-01-01T00:10:00Z',
    last_activity_source: 'schedule',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

const mockUseConversations = vi.fn()
const mockUseConversationPages = vi.fn()

vi.mock('@/lib/hooks/use-conversations', () => ({
  useConversations: (...args: unknown[]) => mockUseConversations(...args),
  useConversationPages: (...args: unknown[]) => mockUseConversationPages(...args),
  useCreateConversation: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ id: 'conv-new' }),
    isPending: false,
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

describe('ConversationList', () => {
  beforeEach(() => {
    mockUseConversations.mockReturnValue({ data: [], isLoading: false })
    mockUseConversationPages.mockReturnValue({
      data: { pages: [{ items: mockConversations, next_cursor: null, has_more: false }] },
      isLoading: false,
      hasNextPage: false,
      fetchNextPage: vi.fn(),
      isFetchingNextPage: false,
    })
  })

  it('renders agent name in card header', () => {
    render(<ConversationList agentId="agent-1" agentName="테스트 에이전트" />)
    expect(screen.getByText('테스트 에이전트')).toBeInTheDocument()
  })

  it('renders 대화 section label', () => {
    render(<ConversationList agentId="agent-1" />)
    expect(screen.getByText('대화')).toBeInTheDocument()
  })

  it('renders new conversation button', () => {
    render(<ConversationList agentId="agent-1" />)
    expect(screen.getByRole('button', { name: '새 대화' })).toBeInTheDocument()
  })

  it('renders conversation list from API', async () => {
    render(<ConversationList agentId="agent-1" />)

    await waitFor(() => {
      expect(screen.getByText('Test Conversation')).toBeInTheDocument()
    })
    expect(screen.getByText('Second Conversation')).toBeInTheDocument()
  })

  it('renders conversation links with correct hrefs', async () => {
    render(<ConversationList agentId="agent-1" />)

    await waitFor(() => {
      expect(screen.getByText('Test Conversation')).toBeInTheDocument()
    })

    const links = screen.getAllByRole('link')
    // First link is the settings link, conversation links follow
    const convLinks = links.filter((link) =>
      link.getAttribute('href')?.includes('/conversations/'),
    )
    expect(convLinks[0]).toHaveAttribute('href', '/agents/agent-1/conversations/conv-1')
    expect(convLinks[1]).toHaveAttribute('href', '/agents/agent-1/conversations/conv-2')
  })

  it('shows unread badge for scheduled results', async () => {
    render(<ConversationList agentId="agent-1" />)

    await waitFor(() => {
      expect(screen.getByText('Second Conversation')).toBeInTheDocument()
    })
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('shows loading skeletons initially', () => {
    mockUseConversationPages.mockReturnValue({
      data: undefined,
      isLoading: true,
      hasNextPage: false,
      fetchNextPage: vi.fn(),
      isFetchingNextPage: false,
    })
    const { container } = render(<ConversationList agentId="agent-1" />)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('creates new conversation when button clicked', async () => {
    const user = userEvent.setup()
    render(<ConversationList agentId="agent-1" />)

    await waitFor(() => {
      expect(screen.getByText('Test Conversation')).toBeInTheDocument()
    })

    const newButton = screen.getByRole('button', { name: '새 대화' })
    await user.click(newButton)

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith('/agents/agent-1/conversations/conv-new')
    })
  })

  it('loads another server page when more conversations are available', async () => {
    const user = userEvent.setup()
    const fetchNextPage = vi.fn()
    mockUseConversationPages.mockReturnValue({
      data: { pages: [{ items: mockConversations, next_cursor: 'next', has_more: true }] },
      isLoading: false,
      hasNextPage: true,
      fetchNextPage,
      isFetchingNextPage: false,
    })

    render(<ConversationList agentId="agent-1" />)

    await user.click(screen.getByRole('button', { name: '더 보기' }))

    expect(fetchNextPage).toHaveBeenCalled()
  })
})
