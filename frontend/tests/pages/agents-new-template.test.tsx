import { render, screen } from '../test-utils'
import TemplateSelectionPage from '@/app/agents/new/template/page'
import { mockTemplateList } from '../mocks/fixtures'

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
}))

const mockUseTemplates = vi.fn()
const mockUseModels = vi.fn()

vi.mock('@/lib/hooks/use-templates', () => ({
  useTemplates: (...args: unknown[]) => mockUseTemplates(...args),
}))

vi.mock('@/lib/hooks/use-models', () => ({
  useModels: () => mockUseModels(),
}))

const mockCreateAgentFn = vi.fn()

vi.mock('@/lib/hooks/use-agents', () => ({
  useCreateAgent: () => ({
    mutateAsync: mockCreateAgentFn,
  }),
}))

describe('TemplateSelectionPage', () => {
  beforeEach(() => {
    mockUseTemplates.mockReturnValue({ data: undefined, isLoading: false })
    mockUseModels.mockReturnValue({ data: undefined })
    mockCreateAgentFn.mockClear()
  })

  it('renders page header', () => {
    render(<TemplateSelectionPage />)
    expect(screen.getByText('템플릿으로 만들기')).toBeInTheDocument()
  })

  it('renders loading state with skeletons', () => {
    mockUseTemplates.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(<TemplateSelectionPage />)
    // Skeletons are rendered (4 skeleton cards)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('renders template cards when data loaded', () => {
    mockUseTemplates.mockReturnValue({
      data: mockTemplateList,
      isLoading: false,
    })
    render(<TemplateSelectionPage />)
    expect(screen.getByText('Research Assistant')).toBeInTheDocument()
    expect(screen.getByText('Writing Helper')).toBeInTheDocument()
  })

  it('shows template descriptions', () => {
    mockUseTemplates.mockReturnValue({
      data: mockTemplateList,
      isLoading: false,
    })
    render(<TemplateSelectionPage />)
    expect(screen.getByText('An agent that helps with research')).toBeInTheDocument()
    expect(screen.getByText('Helps with writing tasks')).toBeInTheDocument()
  })

  it('shows recommended tools on template cards', () => {
    mockUseTemplates.mockReturnValue({
      data: mockTemplateList,
      isLoading: false,
    })
    render(<TemplateSelectionPage />)
    // Both templates have recommended_tools with "Web Search", shown as "도구: Web Search"
    const toolTexts = screen.getAllByText('도구: Web Search')
    expect(toolTexts.length).toBeGreaterThanOrEqual(1)
  })

  it('renders category tabs', () => {
    render(<TemplateSelectionPage />)
    expect(screen.getByText('전체')).toBeInTheDocument()
    expect(screen.getByText('생산성')).toBeInTheDocument()
    expect(screen.getByText('커뮤니케이션')).toBeInTheDocument()
    expect(screen.getByText('데이터')).toBeInTheDocument()
  })

  it('shows empty state when no templates in category', () => {
    mockUseTemplates.mockReturnValue({ data: [], isLoading: false })
    render(<TemplateSelectionPage />)
    expect(screen.getByText('이 카테고리에 템플릿이 없습니다.')).toBeInTheDocument()
  })

  it('calls createAgent when template create button is clicked', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    mockUseTemplates.mockReturnValue({
      data: mockTemplateList,
      isLoading: false,
    })
    mockUseModels.mockReturnValue({
      data: [{ id: 'model-1', is_default: true }],
    })
    mockCreateAgentFn.mockResolvedValue({ id: 'agent-new' })

    render(<TemplateSelectionPage />)

    const createButtons = screen.getAllByText('이 템플릿으로 생성')
    await user.click(createButtons[0])

    expect(mockCreateAgentFn).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Research Assistant',
        system_prompt: 'You are a research assistant.',
        template_id: 'template-1',
      }),
    )
  })

  it('shows create button text on each template card', () => {
    mockUseTemplates.mockReturnValue({
      data: mockTemplateList,
      isLoading: false,
    })
    render(<TemplateSelectionPage />)
    const createButtons = screen.getAllByText('이 템플릿으로 생성')
    expect(createButtons.length).toBe(mockTemplateList.length)
  })
})
