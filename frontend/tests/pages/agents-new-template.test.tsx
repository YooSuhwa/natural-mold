import { render, screen } from '../test-utils'
import TemplateSelectionPage from '@/app/agents/new/template/page'
import { mockTemplateList } from '../mocks/fixtures'

const mockRouterPush = vi.hoisted(() => vi.fn())
const mockSearchParams = vi.hoisted(() => ({
  value: new URLSearchParams(),
}))

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
  useRouter: () => ({
    push: mockRouterPush,
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
  usePathname: () => '/',
  useParams: () => ({}),
  useSearchParams: () => mockSearchParams.value,
}))

const mockUseTemplates = vi.fn()
const mockUseModels = vi.fn()
const mockUseAgentBlueprints = vi.fn()

vi.mock('@/lib/hooks/use-templates', () => ({
  useTemplates: (...args: unknown[]) => mockUseTemplates(...args),
}))

vi.mock('@/lib/hooks/use-models', () => ({
  useModels: () => mockUseModels(),
}))

const mockCreateAgentFn = vi.fn()
const mockCreateAgentFromBlueprintFn = vi.fn()

vi.mock('@/lib/hooks/use-agents', () => ({
  useCreateAgent: () => ({
    mutateAsync: mockCreateAgentFn,
  }),
}))

vi.mock('@/lib/hooks/use-marketplace', () => ({
  useAgentBlueprints: () => mockUseAgentBlueprints(),
  useCreateAgentFromBlueprint: () => ({
    mutateAsync: mockCreateAgentFromBlueprintFn,
  }),
}))

describe('TemplateSelectionPage', () => {
  beforeEach(() => {
    mockUseTemplates.mockReturnValue({ data: undefined, isLoading: false })
    mockUseModels.mockReturnValue({ data: undefined })
    mockUseAgentBlueprints.mockReturnValue({ data: [], isLoading: false })
    mockCreateAgentFn.mockClear()
    mockCreateAgentFromBlueprintFn.mockClear()
    mockRouterPush.mockClear()
    mockSearchParams.value = new URLSearchParams()
  })

  it('renders page header', () => {
    render(<TemplateSelectionPage />)
    expect(screen.getByText('템플릿으로 시작하기')).toBeInTheDocument()
  })

  it('renders loading state with skeletons', () => {
    mockUseTemplates.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(<TemplateSelectionPage />)
    // Skeletons are rendered (4 skeleton cards)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('renders template cards when data loaded', async () => {
    mockUseTemplates.mockReturnValue({
      data: mockTemplateList,
      isLoading: false,
    })
    render(<TemplateSelectionPage />)
    expect(await screen.findByText('Research Assistant')).toBeInTheDocument()
    expect(screen.getByText('Writing Helper')).toBeInTheDocument()
  })

  it('shows template descriptions', async () => {
    mockUseTemplates.mockReturnValue({
      data: mockTemplateList,
      isLoading: false,
    })
    render(<TemplateSelectionPage />)
    expect(await screen.findByText('An agent that helps with research')).toBeInTheDocument()
    expect(screen.getByText('Helps with writing tasks')).toBeInTheDocument()
  })

  it('shows recommended tools on template cards', async () => {
    mockUseTemplates.mockReturnValue({
      data: mockTemplateList,
      isLoading: false,
    })
    render(<TemplateSelectionPage />)
    // Both templates have recommended_tools with "Web Search", shown as badge chips
    const toolTexts = await screen.findAllByText('Web Search')
    expect(toolTexts.length).toBeGreaterThanOrEqual(1)
  })

  it('renders category tabs', () => {
    render(<TemplateSelectionPage />)
    expect(screen.getByText('전체')).toBeInTheDocument()
    expect(screen.getByText('생산성')).toBeInTheDocument()
    expect(screen.getByText('커뮤니케이션')).toBeInTheDocument()
    expect(screen.getByText('데이터')).toBeInTheDocument()
  })

  it('shows empty state when no templates in category', async () => {
    mockUseTemplates.mockReturnValue({ data: [], isLoading: false })
    render(<TemplateSelectionPage />)
    expect(await screen.findByText('이 카테고리에 템플릿이 없습니다.')).toBeInTheDocument()
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

    const createButtons = await screen.findAllByRole('button', { name: /이 템플릿으로 생성/ })
    await user.click(createButtons[0])

    expect(mockCreateAgentFn).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Research Assistant',
        system_prompt: 'You are a research assistant.',
        template_id: 'template-1',
      }),
    )
  })

  it('creates an agent from a blueprint without overriding the blueprint model', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    mockUseTemplates.mockReturnValue({ data: [], isLoading: false })
    mockUseModels.mockReturnValue({
      data: [{ id: 'default-model', is_default: true }],
    })
    mockUseAgentBlueprints.mockReturnValue({
      data: [
        {
          id: 'blueprint-1',
          name: 'Shared Blueprint',
          description: 'Shared by the marketplace',
          icon_id: null,
          tags: [],
          categories: ['productivity'],
          spec: {},
          spec_hash: 'hash',
          source_marketplace_item_id: 'item-1',
          source_marketplace_version_id: 'version-1',
          installation_id: 'installation-1',
          install_status: 'active',
          is_dirty: false,
          created_agent_count: 0,
          created_at: '2026-06-10T00:00:00Z',
          updated_at: '2026-06-10T00:00:00Z',
        },
      ],
      isLoading: false,
    })
    mockCreateAgentFromBlueprintFn.mockResolvedValue({ id: 'agent-from-blueprint' })

    render(<TemplateSelectionPage />)

    await user.click(await screen.findByRole('button', { name: /Shared Blueprint/ }))

    expect(mockCreateAgentFromBlueprintFn).toHaveBeenCalledWith({
      blueprintId: 'blueprint-1',
      body: {
        name: 'Shared Blueprint',
      },
    })
  })

  it('marks the blueprint from the deep link as selected', async () => {
    mockSearchParams.value = new URLSearchParams('blueprintId=blueprint-1')
    mockUseTemplates.mockReturnValue({ data: [], isLoading: false })
    mockUseAgentBlueprints.mockReturnValue({
      data: [
        {
          id: 'blueprint-1',
          name: 'Shared Blueprint',
          description: 'Shared by the marketplace',
          icon_id: null,
          tags: [],
          categories: ['productivity'],
          spec: {},
          spec_hash: 'hash',
          source_marketplace_item_id: 'item-1',
          source_marketplace_version_id: 'version-1',
          installation_id: 'installation-1',
          install_status: 'active',
          is_dirty: false,
          created_agent_count: 0,
          created_at: '2026-06-10T00:00:00Z',
          updated_at: '2026-06-10T00:00:00Z',
        },
      ],
      isLoading: false,
    })

    render(<TemplateSelectionPage />)

    expect(await screen.findByRole('button', { name: /Shared Blueprint/ })).toHaveAttribute(
      'aria-current',
      'true',
    )
  })

  it('shows create button text on each template card', async () => {
    mockUseTemplates.mockReturnValue({
      data: mockTemplateList,
      isLoading: false,
    })
    render(<TemplateSelectionPage />)
    const createButtons = await screen.findAllByText('시작')
    expect(createButtons.length).toBe(mockTemplateList.length)
  })

  it('uses one count surface and pins the custom-template CTA to the page bottom', () => {
    mockUseTemplates.mockReturnValue({
      data: mockTemplateList,
      isLoading: false,
    })
    render(<TemplateSelectionPage />)

    expect(screen.getAllByText(`${mockTemplateList.length}개`)).toHaveLength(1)
    expect(screen.queryByText('Agent Gallery')).not.toBeInTheDocument()
    expect(screen.getByText('원하는 템플릿이 없나요?').closest('a')).toHaveClass('mt-auto')
  })
})
