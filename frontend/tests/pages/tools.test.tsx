import { render, screen, userEvent } from '../test-utils'
import ToolsPage from '@/app/tools/page'

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
const mockUseToolTypes = vi.fn()
const mockDeleteTool = vi.fn()

vi.mock('@/lib/hooks/use-tools', () => ({
  useTools: () => mockUseTools(),
  useTool: () => ({ data: undefined, isLoading: false }),
  useToolTypes: () => mockUseToolTypes(),
  useToolType: () => ({ data: undefined, isLoading: false }),
  useDeleteTool: () => ({
    mutate: mockDeleteTool,
    isPending: false,
  }),
  // ``useCreateCustomTool``은 ``useCreateTool``로 통합되어 사라짐 — 테스트
  // 셋업이 stale이라 mock은 보존하되 실제 hook으로 alias.
  useCreateTool: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useUpdateTool: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useRunTool: () => ({ mutateAsync: vi.fn(), isPending: false }),
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

vi.mock('@/lib/hooks/use-credentials', () => ({
  useCredentials: () => ({ data: [], isLoading: false }),
  useCredentialProviders: () => ({ data: [] }),
}))

// 페이지가 카탈로그 그리드를 렌더하는데 그 안에서 detail 카드 테스트를 따로
// 책임지므로 stub. 페이지 단위는 단일 탭 구조와 설치됨 전환만 검증한다.
vi.mock('@/app/tools/_components/tool-catalog', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/app/tools/_components/tool-catalog')>()
  return {
    ...actual,
    ToolCatalog: ({ category, search }: { category: string; search: string }) => (
      <div data-category={category} data-search={search} data-testid="tool-catalog" />
    ),
  }
})
vi.mock('@/app/tools/_components/tool-create-dialog', () => ({
  ToolCreateDialog: () => null,
}))
vi.mock('@/app/tools/_components/tool-detail-dialog', () => ({
  ToolDetailDialog: () => null,
}))

/**
 * 페이지 구조: 헤더 + 패널 안 단일 탭 줄[전체/카테고리/설치됨] + 검색 +
 * 카탈로그 그리드 또는 설치됨 카드 그리드. 카드 detail은 tool-catalog 컴포넌트
 * 단위 테스트가 책임진다.
 */
describe('ToolsPage', () => {
  beforeEach(() => {
    mockUseTools.mockReturnValue({ data: undefined, isLoading: false })
    mockUseToolTypes.mockReturnValue({ data: [], isLoading: false })
    mockDeleteTool.mockClear()
  })

  it('renders page header with title', () => {
    render(<ToolsPage />)
    expect(screen.getByText('도구')).toBeInTheDocument()
  })

  it('renders catalog categories and installed in one tab row', () => {
    mockUseToolTypes.mockReturnValue({
      data: [
        {
          key: 'web_search',
          display_name: '웹 검색',
          description: '웹을 검색합니다.',
          icon_id: 'search',
          category: 'search',
          parameters: [],
          credential_definition_keys: [],
          requires_credential: false,
        },
        {
          key: 'http_request',
          display_name: 'HTTP 요청',
          description: '외부 API를 호출합니다.',
          icon_id: 'globe',
          category: 'automation',
          parameters: [],
          credential_definition_keys: [],
          requires_credential: false,
        },
      ],
      isLoading: false,
    })

    render(<ToolsPage />)
    expect(screen.getByRole('tab', { name: '전체 2개' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '검색' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '자동화' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '설치됨' })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /카탈로그/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /관리/ })).not.toBeInTheDocument()
  })

  it('shows the active tab count in one place', () => {
    mockUseToolTypes.mockReturnValue({
      data: [
        {
          key: 'web_search',
          display_name: '웹 검색',
          description: '웹을 검색합니다.',
          icon_id: 'search',
          category: 'search',
          parameters: [],
          credential_definition_keys: [],
          requires_credential: false,
        },
        {
          key: 'web_scraper',
          display_name: '웹 스크래퍼',
          description: '웹페이지를 읽습니다.',
          icon_id: 'globe',
          category: 'search',
          parameters: [],
          credential_definition_keys: [],
          requires_credential: false,
        },
      ],
      isLoading: false,
    })

    render(<ToolsPage />)

    const activeTab = screen.getByRole('tab', { name: '전체 2개' })
    expect(activeTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getAllByText('2개')).toHaveLength(1)
  })

  it('mounts the ToolCatalog stub on the catalog tab', () => {
    render(<ToolsPage />)
    expect(screen.getByTestId('tool-catalog')).toBeInTheDocument()
  })

  it('shows empty state when no tools after switching to 설치됨', async () => {
    mockUseTools.mockReturnValue({ data: [], isLoading: false })
    const user = userEvent.setup()
    render(<ToolsPage />)
    await user.click(screen.getByRole('tab', { name: /설치됨/ }))
    expect(screen.getByText('아직 도구가 없어요')).toBeInTheDocument()
  })

  it('renders installed tools as catalog-style cards', async () => {
    mockUseToolTypes.mockReturnValue({
      data: [
        {
          key: 'web_search',
          display_name: '웹 검색',
          description: '웹을 검색합니다.',
          icon_id: 'search',
          category: 'search',
          parameters: [],
          credential_definition_keys: [],
          requires_credential: false,
        },
      ],
      isLoading: false,
    })
    mockUseTools.mockReturnValue({
      data: [
        {
          id: 'tool-1',
          user_id: 'user-1',
          definition_key: 'web_search',
          name: '웹 검색',
          description: '웹을 검색합니다.',
          parameters: {},
          credential_id: null,
          enabled: true,
          last_used_at: null,
          created_at: '2026-05-01T00:00:00.000Z',
          updated_at: '2026-05-01T00:00:00.000Z',
        },
      ],
      isLoading: false,
    })

    const user = userEvent.setup()
    render(<ToolsPage />)
    await user.click(screen.getByRole('tab', { name: /설치됨/ }))

    const card = screen.getByRole('button', { name: /웹 검색/ })
    expect(card).toHaveClass('moldy-resource-card')
    expect(card).toHaveClass('moldy-tone-card-sky')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  // 카탈로그/검색/필터/뱃지/삭제 버튼 등 detail UI는 모두 tool-catalog 단위
  // 테스트와 e2e가 책임진다 (페이지 단위에서 제외).
})
