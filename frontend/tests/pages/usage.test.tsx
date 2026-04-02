import { render, screen } from "../test-utils"
import UsagePage from "@/app/usage/page"
import { mockUsageSummary } from "../mocks/fixtures"

vi.mock("next/link", () => ({
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

vi.mock("@/lib/hooks/use-usage", () => ({
  useUsageSummary: () => mockUseUsageSummary(),
}))

describe("UsagePage", () => {
  beforeEach(() => {
    mockUseUsageSummary.mockReturnValue({ data: undefined, isLoading: false })
  })

  it("renders page header", () => {
    mockUseUsageSummary.mockReturnValue({ data: undefined, isLoading: false })
    render(<UsagePage />)
    expect(screen.getByText("토큰 사용량")).toBeInTheDocument()
  })

  it("renders loading skeletons", () => {
    mockUseUsageSummary.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(<UsagePage />)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("shows empty state when no usage data", () => {
    mockUseUsageSummary.mockReturnValue({
      data: { ...mockUsageSummary, total_tokens: 0 },
      isLoading: false,
    })
    render(<UsagePage />)
    expect(
      screen.getByText("아직 사용 내역이 없습니다.")
    ).toBeInTheDocument()
  })

  it("renders summary cards with usage data", () => {
    mockUseUsageSummary.mockReturnValue({
      data: mockUsageSummary,
      isLoading: false,
    })
    render(<UsagePage />)
    expect(screen.getByText("총 토큰")).toBeInTheDocument()
    expect(screen.getByText("추정 비용")).toBeInTheDocument()
    expect(screen.getByText("입력 토큰")).toBeInTheDocument()
    expect(screen.getByText("출력 토큰")).toBeInTheDocument()
    expect(screen.getByText("150,000")).toBeInTheDocument()
    expect(screen.getByText("$1.25")).toBeInTheDocument()
    // 100,000 appears in both summary card and per-agent table
    expect(screen.getAllByText("100,000").length).toBeGreaterThanOrEqual(1)
    // 50,000 also appears in both summary card and per-agent table
    expect(screen.getAllByText("50,000").length).toBeGreaterThanOrEqual(1)
  })

  it("renders per-agent breakdown table", () => {
    mockUseUsageSummary.mockReturnValue({
      data: mockUsageSummary,
      isLoading: false,
    })
    render(<UsagePage />)
    expect(screen.getByText("에이전트별 사용량")).toBeInTheDocument()
    expect(screen.getByText("Test Agent")).toBeInTheDocument()
    expect(screen.getByText("Second Agent")).toBeInTheDocument()
    expect(screen.getByText("$0.85")).toBeInTheDocument()
    expect(screen.getByText("$0.40")).toBeInTheDocument()
  })

  it("shows empty state when usage is undefined", () => {
    mockUseUsageSummary.mockReturnValue({
      data: undefined,
      isLoading: false,
    })
    render(<UsagePage />)
    expect(
      screen.getByText("아직 사용 내역이 없습니다.")
    ).toBeInTheDocument()
  })
})
