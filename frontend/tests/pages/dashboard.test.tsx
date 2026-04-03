import { render, screen } from '../test-utils'
import DashboardPage from '@/app/page'
import { mockAgentList, mockUsageSummary } from '../mocks/fixtures'

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

const mockUseAgents = vi.fn()
const mockUseUsageSummary = vi.fn()
const mockToggleFavorite = vi.fn()

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => mockUseAgents(),
  useToggleFavorite: () => ({ mutate: mockToggleFavorite }),
}))

vi.mock('@/lib/hooks/use-usage', () => ({
  useUsageSummary: () => mockUseUsageSummary(),
}))

describe('DashboardPage', () => {
  beforeEach(() => {
    mockUseAgents.mockReturnValue({ data: undefined, isLoading: false })
    mockUseUsageSummary.mockReturnValue({ data: undefined })
  })

  it('renders loading skeletons when agents are loading', () => {
    mockUseAgents.mockReturnValue({ data: undefined, isLoading: true })
    render(<DashboardPage />)
    expect(screen.getByText('내 에이전트')).toBeInTheDocument()
  })

  it('renders agent cards when data is loaded', () => {
    mockUseAgents.mockReturnValue({
      data: mockAgentList,
      isLoading: false,
    })
    render(<DashboardPage />)
    expect(screen.getByText('Test Agent')).toBeInTheDocument()
    expect(screen.getByText('Second Agent')).toBeInTheDocument()
  })

  it('renders empty state when no agents', () => {
    mockUseAgents.mockReturnValue({ data: [], isLoading: false })
    render(<DashboardPage />)
    expect(screen.getByText('첫 에이전트를 만들어보세요')).toBeInTheDocument()
  })

  it('shows quick action cards linking to creation pages', () => {
    render(<DashboardPage />)
    expect(screen.getByText('대화로 만들기')).toBeInTheDocument()
    expect(screen.getByText('템플릿으로 만들기')).toBeInTheDocument()

    const conversationalLink = screen.getByText('대화로 만들기').closest('a')
    expect(conversationalLink).toHaveAttribute('href', '/agents/new/conversational')

    const templateLink = screen.getByText('템플릿으로 만들기').closest('a')
    expect(templateLink).toHaveAttribute('href', '/agents/new/template')
  })

  it('shows hero section with new agent button', () => {
    render(<DashboardPage />)
    expect(screen.getByText('안녕하세요!')).toBeInTheDocument()
    const newAgentLink = screen.getByText('새 에이전트').closest('a')
    expect(newAgentLink).toHaveAttribute('href', '/agents/new')
  })

  it('shows usage summary when data is available', () => {
    mockUseAgents.mockReturnValue({ data: [], isLoading: false })
    mockUseUsageSummary.mockReturnValue({ data: mockUsageSummary })
    render(<DashboardPage />)
    expect(screen.getByText('이번 달 사용량')).toBeInTheDocument()
    expect(screen.getByText('150,000')).toBeInTheDocument()
    expect(screen.getByText('$1.25')).toBeInTheDocument()
  })

  it('does not show usage summary when total_tokens is 0', () => {
    mockUseUsageSummary.mockReturnValue({
      data: { ...mockUsageSummary, total_tokens: 0 },
    })
    render(<DashboardPage />)
    expect(screen.queryByText('이번 달 사용량')).not.toBeInTheDocument()
  })

  it('shows agent count in header section', () => {
    mockUseAgents.mockReturnValue({
      data: mockAgentList,
      isLoading: false,
    })
    render(<DashboardPage />)
    expect(screen.getByText('내 에이전트')).toBeInTheDocument()
  })
})
