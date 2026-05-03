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
const mockDeleteTool = vi.fn()

vi.mock('@/lib/hooks/use-tools', () => ({
  useTools: () => mockUseTools(),
  useTool: () => ({ data: undefined, isLoading: false }),
  useToolTypes: () => ({ data: [], isLoading: false }),
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

// 페이지가 catalog 탭에서 ToolCatalog를 렌더하는데 그 안에서 무거운 의존성을
// 끌고 오므로 stub. detail은 catalog 컴포넌트 단위 테스트가 책임진다.
vi.mock('@/components/tool/tool-catalog', () => ({
  ToolCatalog: () => <div data-testid="tool-catalog" />,
}))
vi.mock('@/components/tool/tool-create-dialog', () => ({
  ToolCreateDialog: () => null,
}))
vi.mock('@/components/tool/tool-detail-dialog', () => ({
  ToolDetailDialog: () => null,
}))

/**
 * 페이지 구조 (M10 이후): PageHeader "Tools" 영문 + Tabs[Catalog/Manage] +
 * DataTable + EmptyState. 옛 테스트의 한국어/필터 칩/검색은 모두
 * tool-catalog 컴포넌트 안으로 흡수됐다. 페이지 단위는 헤더 + 탭 +
 * EmptyState만 책임진다.
 */
describe('ToolsPage', () => {
  beforeEach(() => {
    mockUseTools.mockReturnValue({ data: undefined, isLoading: false })
    mockDeleteTool.mockClear()
  })

  it('renders page header with title', () => {
    render(<ToolsPage />)
    expect(screen.getByText('Tools')).toBeInTheDocument()
  })

  it('renders Catalog + Manage tabs', () => {
    render(<ToolsPage />)
    expect(screen.getByRole('tab', { name: /Catalog/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Manage/ })).toBeInTheDocument()
  })

  it('mounts the ToolCatalog stub on the catalog tab', () => {
    render(<ToolsPage />)
    expect(screen.getByTestId('tool-catalog')).toBeInTheDocument()
  })

  it('shows empty state when no tools after switching to Manage', async () => {
    mockUseTools.mockReturnValue({ data: [], isLoading: false })
    const user = userEvent.setup()
    render(<ToolsPage />)
    await user.click(screen.getByRole('tab', { name: /Manage/ }))
    expect(screen.getByText('No tools yet')).toBeInTheDocument()
  })

  // 카탈로그/검색/필터/뱃지/삭제 버튼 등 detail UI는 모두 tool-catalog 및
  // DataTable 단위 테스트와 e2e가 책임진다 (페이지 단위에서 제외).
})
