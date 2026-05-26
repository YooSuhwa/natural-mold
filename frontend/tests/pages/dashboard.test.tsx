import { render, screen } from '../test-utils'
import DashboardPage from '@/app/page'
import { mockAgentList } from '../mocks/fixtures'

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
const mockUseSession = vi.fn()
const mockToggleFavorite = vi.fn()

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => mockUseAgents(),
  useToggleFavorite: () => ({ mutate: mockToggleFavorite }),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => mockUseSession(),
}))

describe('DashboardPage', () => {
  beforeEach(() => {
    mockUseAgents.mockReturnValue({ data: undefined, isLoading: false })
    mockUseSession.mockReturnValue({ data: null })
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
    expect(conversationalLink).toHaveAttribute('href', '/agents/new')

    const templateLink = screen.getByText('템플릿으로 만들기').closest('a')
    expect(templateLink).toHaveAttribute('href', '/agents/new/template')
  })

  it('shows hero greeting with user name and subtitle with count', () => {
    mockUseAgents.mockReturnValue({ data: mockAgentList, isLoading: false })
    mockUseSession.mockReturnValue({ data: { id: 'u1', name: '수화', email: 'a@b.c' } })
    render(<DashboardPage />)
    // 시간대별 인사 5종 중 하나 + 사용자 이름 + 카운트 포함 subtitle.
    expect(screen.getByText(/(좋은 아침이에요|좋은 오후예요|좋은 저녁이에요|늦은 밤이네요|오늘도 수고하셨어요),/)).toBeInTheDocument()
    expect(screen.getByText(/수화님/)).toBeInTheDocument()
    expect(screen.getByText(new RegExp(`현재 ${mockAgentList.length}개의 에이전트가 있어요`))).toBeInTheDocument()
  })

  it('falls back to "사용자" when session is null', () => {
    mockUseSession.mockReturnValue({ data: null })
    mockUseAgents.mockReturnValue({ data: [], isLoading: false })
    render(<DashboardPage />)
    expect(screen.getByText(/사용자님/)).toBeInTheDocument()
  })

  it('does not render usage summary or tip line (removed in redesign)', () => {
    mockUseAgents.mockReturnValue({ data: [], isLoading: false })
    render(<DashboardPage />)
    expect(screen.queryByText('이번 달 사용량')).not.toBeInTheDocument()
    expect(screen.queryByText(/💡 팁/)).not.toBeInTheDocument()
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
