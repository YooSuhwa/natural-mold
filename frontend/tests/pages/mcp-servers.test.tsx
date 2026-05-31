import { render, screen, userEvent } from '../test-utils'
import McpServersPage from '@/app/mcp-servers/page'
import type { HealthCheckEntry } from '@/lib/types/health'
import type { McpServer } from '@/lib/types/mcp'

const mockUseMcpServers = vi.fn()
const mockUseMcpHealth = vi.fn()
const mockUseRunHealthCheck = vi.fn()
const mockUseExportMcpServers = vi.fn()

vi.mock('@/lib/hooks/use-mcp-servers', () => ({
  useMcpServers: () => mockUseMcpServers(),
  useExportMcpServers: () => mockUseExportMcpServers(),
}))

vi.mock('@/lib/hooks/use-health', () => ({
  useMcpHealth: () => mockUseMcpHealth(),
  useRunHealthCheck: () => mockUseRunHealthCheck(),
}))

vi.mock('@/components/mcp/mcp-server-wizard', () => ({
  McpServerWizard: () => null,
}))

vi.mock('@/components/mcp/mcp-server-detail-dialog', () => ({
  McpServerDetailDialog: () => null,
}))

vi.mock('@/components/mcp/mcp-import-dialog', () => ({
  McpImportDialog: () => null,
}))

const server: McpServer = {
  id: 'mcp-1',
  user_id: 'user-1',
  name: 'GitHub MCP',
  description: 'GitHub 이슈와 PR을 조회합니다.',
  transport: 'stdio',
  url: null,
  command: 'npx',
  args: ['-y', '@modelcontextprotocol/server-github'],
  env_vars: {},
  headers: {},
  credential_id: null,
  status: 'connected',
  last_pinged_at: '2026-05-01T00:00:00Z',
  last_tool_count: 12,
  last_error: null,
  is_system: false,
  health_status: 'healthy',
  health_polled_at: '2026-05-01T00:00:00Z',
  health_message: null,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-02T00:00:00Z',
}

const health: HealthCheckEntry = {
  id: 'health-1',
  target_kind: 'mcp_server',
  target_id: 'mcp-1',
  status: 'healthy',
  latency_ms: 120,
  error_kind: null,
  error_message: null,
  checked_at: '2026-05-02T00:00:00Z',
}

describe('McpServersPage', () => {
  beforeEach(() => {
    mockUseMcpServers.mockReturnValue({ data: [server], isLoading: false })
    mockUseMcpHealth.mockReturnValue({ data: [health] })
    mockUseRunHealthCheck.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })
    mockUseExportMcpServers.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })
  })

  it('uses a tabbed card panel instead of the old server table', () => {
    render(<McpServersPage />)

    expect(screen.getByRole('tab', { name: '전체 1개' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('서버 검색')).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '이름' })).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByText('GitHub 이슈와 PR을 조회합니다.')).toBeInTheDocument()
    expect(screen.getByText('12개 도구')).toBeInTheDocument()
  })

  it('renders MCP servers as status cards by default', () => {
    render(<McpServersPage />)

    const card = screen.getByText('GitHub MCP').closest('[role="button"]')
    expect(card).toHaveClass('border-transparent')
    expect(card?.className).toMatch(/\bbg-(violet|sky|emerald|amber|rose)-50\/75\b/)
    expect(screen.getByTestId('check-now-mcp-1')).toBeInTheDocument()
  })

  it('filters MCP servers from the shared status tabs', async () => {
    const user = userEvent.setup()
    render(<McpServersPage />)

    await user.click(screen.getByRole('tab', { name: /연결됨/ }))

    expect(screen.getByRole('tab', { name: '연결됨 1개' })).toHaveAttribute('aria-selected', 'true')
  })
})
