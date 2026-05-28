import { render, screen } from '../test-utils'
import UsagePage from '@/app/usage/page'
import { mockUsageSummary } from '../mocks/fixtures'

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

const mockUseUsageSummary = vi.fn()
const mockUseDailyAggregate = vi.fn()

vi.mock('@/lib/hooks/use-usage', () => ({
  useUsageSummary: () => mockUseUsageSummary(),
  useDailyAggregate: () => mockUseDailyAggregate(),
}))

// SpendLineChart/SpendBarChart는 recharts 의존성으로 jsdom에서 렌더 비용 큼.
// Container만 stub해도 페이지 레이아웃 검증에는 충분.
vi.mock('@/components/usage/spend-line-chart', () => ({
  SpendLineChart: () => <div data-testid="spend-line-chart" />,
}))
vi.mock('@/components/usage/spend-bar-chart', () => ({
  SpendBarChart: () => <div data-testid="spend-bar-chart" />,
}))

describe('UsagePage', () => {
  beforeEach(() => {
    mockUseUsageSummary.mockReturnValue({ data: undefined, isLoading: false })
    mockUseDailyAggregate.mockReturnValue({ data: undefined, isLoading: false })
  })

  it('renders page header', () => {
    render(<UsagePage />)
    expect(screen.getByText('사용량')).toBeInTheDocument()
  })

  it('renders 4 summary cards (cost / tokens / requests / avg)', () => {
    mockUseUsageSummary.mockReturnValue({ data: mockUsageSummary, isLoading: false })
    render(<UsagePage />)
    expect(screen.getByText('이번 달 비용')).toBeInTheDocument()
    expect(screen.getByText('이번 달 토큰')).toBeInTheDocument()
    expect(screen.getByText('이번 달 요청')).toBeInTheDocument()
    expect(screen.getByText('평균 비용/요청')).toBeInTheDocument()
  })

  it('renders filter bar with target-kind / group-by / metric tabs', () => {
    render(<UsagePage />)
    expect(screen.getByTestId('usage-filter-bar')).toBeInTheDocument()
    expect(screen.getByTestId('target-kind-tabs')).toBeInTheDocument()
    expect(screen.getByTestId('group-by-tabs')).toBeInTheDocument()
    expect(screen.getByTestId('metric-tabs')).toBeInTheDocument()
  })

  it('renders chart skeleton while daily aggregate is loading', () => {
    mockUseDailyAggregate.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(<UsagePage />)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('shows empty state when daily entries are empty', () => {
    mockUseDailyAggregate.mockReturnValue({ data: [], isLoading: false })
    render(<UsagePage />)
    // Chart + Table 두 군데에 동일 EmptyState가 노출됨 — 둘 다 존재하면 OK.
    expect(screen.getAllByText('아직 사용 내역이 없습니다.').length).toBeGreaterThanOrEqual(1)
  })

  it('renders chart when daily entries are present', () => {
    mockUseDailyAggregate.mockReturnValue({
      data: [
        {
          bucket: '2026-04-30',
          target_kind: 'user',
          target_id: 'u-1',
          target_label: 'me',
          input_tokens: 100,
          output_tokens: 50,
          total_tokens: 150,
          request_count: 1,
          estimated_cost_usd: 0.001,
        },
      ],
      isLoading: false,
    })
    render(<UsagePage />)
    // 차트 컴포넌트 stub이 렌더되는지 확인 — 빈 상태 EmptyState가 노출되지 않음.
    expect(screen.queryByText('아직 사용 내역이 없습니다.')).not.toBeInTheDocument()
  })
})
