import { render, screen, waitFor } from '../test-utils'
import AgentPage from '@/app/agents/[agentId]/page'
import { mockConversationPage } from '../mocks/fixtures'

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

const mockReplace = vi.fn()

// Override: needs named ``mockReplace`` spy for redirect-assertion tests.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: mockReplace }),
}))

const mockConversationsApi = {
  page: vi.fn(),
  create: vi.fn(),
}

vi.mock('@/lib/api/conversations', () => ({
  conversationsApi: {
    page: (...args: unknown[]) => mockConversationsApi.page(...args),
    create: (...args: unknown[]) => mockConversationsApi.create(...args),
  },
}))

// Mock React.use() for params Promise
vi.mock('react', async () => {
  const actual = await vi.importActual('react')
  return {
    ...actual,
    use: (value: unknown) => {
      if (value && typeof value === 'object' && 'agentId' in value) return value
      return (actual as Record<string, unknown>).use(value)
    },
  }
})

describe('AgentPage (redirect)', () => {
  beforeEach(() => {
    mockReplace.mockClear()
    mockConversationsApi.page.mockClear()
    mockConversationsApi.create.mockClear()
  })

  it('redirects to latest conversation when conversations exist', async () => {
    mockConversationsApi.page.mockResolvedValue(mockConversationPage)

    render(<AgentPage params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>} />)

    await waitFor(() => {
      expect(mockConversationsApi.page).toHaveBeenCalledWith('agent-1', { limit: 1 })
      expect(mockReplace).toHaveBeenCalledWith('/agents/agent-1/conversations/conv-1')
    })
  })

  it('redirects to local draft when no conversations exist', async () => {
    mockConversationsApi.page.mockResolvedValue({
      items: [],
      next_cursor: null,
      has_more: false,
    })

    render(<AgentPage params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>} />)

    await waitFor(() => {
      expect(mockConversationsApi.create).not.toHaveBeenCalled()
      expect(mockReplace).toHaveBeenCalledWith('/agents/agent-1/conversations/new')
    })
  })

  it('shows error message when API fails', async () => {
    mockConversationsApi.page.mockRejectedValue(new Error('fail'))

    render(<AgentPage params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>} />)

    await waitFor(() => {
      expect(screen.getByText('대화를 불러오는 데 실패했습니다.')).toBeInTheDocument()
    })
  })
})
