import { act } from 'react'

import { render, screen, userEvent, within } from '../test-utils'
import McpServersPage from '@/app/mcp-servers/page'
import type { HealthCheckEntry } from '@/lib/types/health'
import type { McpServer } from '@/lib/types/mcp'

const mockUseMcpServers = vi.fn()
const mockUseMcpHealth = vi.fn()
const mockUseRunHealthCheck = vi.fn()
const mockUseExportMcpServers = vi.fn()
const mockDetailDialog = vi.hoisted(() => vi.fn(() => null))
const mockReplace = vi.hoisted(() => vi.fn())
const mockSearchParams = vi.hoisted(() => ({
  value: new URLSearchParams(),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: mockReplace,
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
  usePathname: () => '/',
  useParams: () => ({}),
  useSearchParams: () => mockSearchParams.value,
}))

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
  McpServerDetailDialog: (props: {
    serverId: string | null
    open: boolean
    onOpenChange: (open: boolean) => void
  }) => mockDetailDialog(props),
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

const otherServer: McpServer = {
  ...server,
  id: 'mcp-2',
  name: 'Slack MCP',
  description: 'Slack 메시지를 보냅니다.',
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
    mockDetailDialog.mockClear()
    mockReplace.mockClear()
    mockSearchParams.value = new URLSearchParams()
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

    const card = screen.getByText('GitHub MCP').closest('article')
    expect(card).toHaveClass('moldy-resource-card')
    expect(card?.className).toMatch(/\bmoldy-tone-card-sky\b/)
    expect(screen.getByTestId('check-now-mcp-1')).toBeInTheDocument()
  })

  it('filters MCP servers from the shared status tabs', async () => {
    const user = userEvent.setup()
    render(<McpServersPage />)

    await user.click(screen.getByRole('tab', { name: /연결됨/ }))

    expect(screen.getByRole('tab', { name: '연결됨 1개' })).toHaveAttribute('aria-selected', 'true')
  })

  it('opens the detail dialog from the detailId deep link', () => {
    mockSearchParams.value = new URLSearchParams('detailId=mcp-1')

    render(<McpServersPage />)

    expect(mockDetailDialog).toHaveBeenCalledWith(
      expect.objectContaining({
        serverId: 'mcp-1',
        open: true,
      }),
    )
  })

  it('딥링크를 연 뒤 다른 카드를 관리하면 URL을 갱신하고 닫으면 깜빡임 없이 닫힌다', async () => {
    const user = userEvent.setup()
    mockUseMcpServers.mockReturnValue({ data: [server, otherServer], isLoading: false })
    mockSearchParams.value = new URLSearchParams('detailId=mcp-1')

    render(<McpServersPage />)

    // Manage the second card while the deep link still points at mcp-1.
    const otherCard = screen.getByText('Slack MCP').closest('article')!
    await user.click(within(otherCard).getByRole('button', { name: '관리' }))

    // URL is realigned to the manually opened card (no refresh mismatch).
    expect(mockReplace).toHaveBeenCalledWith('/mcp-servers?detailId=mcp-2')
    expect(mockDetailDialog).toHaveBeenLastCalledWith(
      expect.objectContaining({ serverId: 'mcp-2', open: true }),
    )

    // Close the dialog — must stay closed, never flicker back to the deep link.
    act(() => mostRecentOnOpenChange()(false))

    expect(mockDetailDialog).toHaveBeenLastCalledWith(
      expect.objectContaining({ serverId: null, open: false }),
    )
  })
})

function mostRecentOnOpenChange(): (open: boolean) => void {
  const calls = mockDetailDialog.mock.calls
  const lastCall = calls[calls.length - 1]?.[0] as
    | { onOpenChange: (open: boolean) => void }
    | undefined
  if (!lastCall) throw new Error('detail dialog was never rendered')
  return lastCall.onOpenChange
}
