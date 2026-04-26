import { render, screen, userEvent } from '../test-utils'
import ToolsPage from '@/app/tools/page'
import { mockToolList } from '../mocks/fixtures'

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

const mockUseTools = vi.fn()
const mockDeleteTool = vi.fn()

vi.mock('@/lib/hooks/use-tools', () => ({
  useTools: () => mockUseTools(),
  useDeleteTool: () => ({
    mutate: mockDeleteTool,
    isPending: false,
  }),
  useCreateCustomTool: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useUpdateTool: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useToolsByConnection: () => [],
}))

vi.mock('@/lib/hooks/use-connections', () => ({
  useConnections: () => ({ data: [], isLoading: false }),
  useCreateConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDiscoverMcpTools: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useFindOrCreateCustomConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  scopeKey: (scope: unknown) => ['connections', scope],
}))

describe('ToolsPage', () => {
  beforeEach(() => {
    mockUseTools.mockReturnValue({ data: undefined, isLoading: false })
    mockDeleteTool.mockClear()
  })

  it('renders page header with title', () => {
    render(<ToolsPage />)
    expect(screen.getByText('도구 관리')).toBeInTheDocument()
  })

  it('renders loading skeletons', () => {
    mockUseTools.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(<ToolsPage />)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('renders tool list when data loaded', () => {
    mockUseTools.mockReturnValue({ data: mockToolList, isLoading: false })
    render(<ToolsPage />)
    expect(screen.getByText('Web Search')).toBeInTheDocument()
    expect(screen.getByText('My Custom API')).toBeInTheDocument()
  })

  it('shows add tool button in header', () => {
    mockUseTools.mockReturnValue({ data: mockToolList, isLoading: false })
    render(<ToolsPage />)
    const addButtons = screen.getAllByText('도구 추가')
    expect(addButtons.length).toBeGreaterThanOrEqual(1)
  })

  it('shows empty state when no tools', () => {
    mockUseTools.mockReturnValue({ data: [], isLoading: false })
    render(<ToolsPage />)
    expect(screen.getByText('등록된 도구가 없습니다.')).toBeInTheDocument()
  })

  it('shows filter buttons', () => {
    mockUseTools.mockReturnValue({ data: mockToolList, isLoading: false })
    render(<ToolsPage />)
    expect(screen.getByRole('button', { name: /^All/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Built-in/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Pre-built/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Custom/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^MCP/ })).toBeInTheDocument()
  })

  it('shows search input', () => {
    render(<ToolsPage />)
    expect(screen.getByPlaceholderText('도구 검색...')).toBeInTheDocument()
  })

  it('shows search empty state when filtering yields no results', async () => {
    mockUseTools.mockReturnValue({ data: mockToolList, isLoading: false })
    const user = userEvent.setup()
    render(<ToolsPage />)
    const search = screen.getByPlaceholderText('도구 검색...')
    await user.type(search, 'nonexistenttool')
    expect(screen.getByText('검색 결과가 없습니다.')).toBeInTheDocument()
  })

  it('shows tool type badges', () => {
    mockUseTools.mockReturnValue({ data: mockToolList, isLoading: false })
    render(<ToolsPage />)
    // prebuilt tools have a prebuilt-related badge, custom tools have "Custom" badge
    const customBadges = screen.getAllByText('Custom')
    expect(customBadges.length).toBeGreaterThanOrEqual(1)
  })

  it('shows tool descriptions', () => {
    mockUseTools.mockReturnValue({ data: mockToolList, isLoading: false })
    render(<ToolsPage />)
    expect(screen.getByText('Search the web using DuckDuckGo')).toBeInTheDocument()
    expect(screen.getByText('A custom tool')).toBeInTheDocument()
  })

  it('shows system tool indicator for non-prebuilt system tools', () => {
    const toolsWithBuiltin = [
      ...mockToolList,
      {
        ...mockToolList[0],
        id: 'tool-builtin',
        type: 'builtin' as const,
        is_system: true,
        name: 'Builtin System Tool',
      },
    ]
    mockUseTools.mockReturnValue({ data: toolsWithBuiltin, isLoading: false })
    render(<ToolsPage />)
    expect(screen.getByText('시스템 도구')).toBeInTheDocument()
  })

  it('shows delete button for non-system custom tools', () => {
    mockUseTools.mockReturnValue({ data: mockToolList, isLoading: false })
    render(<ToolsPage />)
    // CUSTOM tool은 아이콘 버튼 + aria-label="삭제"로 표시 (i18n key: deleteButton)
    expect(screen.getByLabelText('삭제')).toBeInTheDocument()
  })

  it('shows filter count when tools exist', () => {
    mockUseTools.mockReturnValue({ data: mockToolList, isLoading: false })
    render(<ToolsPage />)
    // All filter should show total count
    const allButton = screen.getByRole('button', { name: /^All/ })
    expect(allButton).toHaveTextContent('2')
  })
})
