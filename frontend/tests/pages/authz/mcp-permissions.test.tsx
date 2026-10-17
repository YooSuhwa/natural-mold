import { render, screen, waitFor } from '../../test-utils'
import { McpServerDetailDialog } from '@/app/mcp-servers/_components/mcp-server-detail-dialog'
import { McpServersPageClient } from '@/app/mcp-servers/_components/mcp-servers-page-client'
import { ApiError } from '@/lib/api/errors'
import type { McpServer } from '@/lib/types/mcp'

/** Plan 13.9 row 6 — MCP: server can_* decides test/edit (hidden from runners);
 * registering (wizard, import) needs cap_register_mcp. */
const state = vi.hoisted(() => ({
  server: null as unknown,
  error: null as unknown,
  permissions: null as string[] | null,
  org: { enforced: false, capabilities: [] as string[] },
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock('@/lib/hooks/use-mcp-servers', () => ({
  useMcpServer: () => ({ data: state.server, isLoading: false, error: state.error }),
  useMcpServers: () => ({ data: state.server ? [state.server] : [], isLoading: false }),
  useExportMcpServers: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateMcpServer: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteMcpServer: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTestMcpServer: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDiscoverMcpTools: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-health', () => ({
  useMcpHealth: () => ({ data: [] }),
  useRunHealthCheck: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/components/shared/health-history-chart', () => ({ HealthHistoryChart: () => null }))
vi.mock('@/components/credential/credential-picker', () => ({
  CredentialPicker: ({ disabled }: { disabled?: boolean }) => (
    <select aria-label="자격 증명" disabled={disabled} />
  ),
}))
vi.mock('@/app/mcp-servers/_components/mcp-server-wizard', () => ({ McpServerWizard: () => null }))
vi.mock('@/app/mcp-servers/_components/mcp-import-dialog', () => ({ McpImportDialog: () => null }))
vi.mock('@/components/marketplace/publish-wizard', () => ({ PublishWizard: () => null }))
vi.mock('@/components/sharing/resource-share-dialog', () => ({ ResourceShareDialog: () => null }))
vi.mock('@/lib/auth/session', () => ({ useSession: () => ({ data: { id: 'me' } }) }))
vi.mock('@/lib/api/resource-permissions', () => ({
  RESOURCE_PERMISSION_BATCH: 100,
  resourcePermissionsApi: {
    list: vi.fn(async (_type: string, ids: string[]) => ({
      items: state.permissions ? ids.map((id) => ({ id, my_permissions: state.permissions })) : [],
    })),
  },
}))
vi.mock('@/lib/api/organizations', () => ({
  meOrganizationsApi: {
    list: vi.fn(async () => [
      {
        id: 'org-1',
        is_active: true,
        roles: [],
        capabilities: state.org.capabilities,
        capabilities_enforced: state.org.enforced,
      },
    ]),
  },
}))

function makeServer(overrides: Partial<McpServer> = {}): McpServer & { tools: [] } {
  return {
    id: 'mcp-1',
    user_id: 'other',
    name: '사내 MCP',
    description: null,
    transport: 'streamable_http',
    url: 'https://mcp.example.test',
    command: null,
    args: [],
    env_vars: {},
    headers: {},
    credential_id: null,
    status: 'active',
    last_pinged_at: null,
    last_tool_count: 0,
    last_error: null,
    is_system: false,
    health_status: null,
    health_polled_at: null,
    health_message: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    tools: [],
    ...overrides,
  }
}

function renderDetail() {
  render(<McpServerDetailDialog serverId="mcp-1" open onOpenChange={vi.fn()} />)
}

describe('MCP server detail by permission level', () => {
  beforeEach(() => {
    state.error = null
    state.permissions = null
  })

  it('owner (legacy body): test, tool refresh, health check, credential and delete', () => {
    state.server = makeServer({ user_id: 'me' })
    renderDetail()
    expect(screen.getByRole('button', { name: /테스트/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /도구 새로고침/ })).toBeInTheDocument()
    expect(screen.getByTestId('mcp-health-check-now')).toBeInTheDocument()
    expect(screen.getByLabelText('자격 증명')).toBeEnabled()
    expect(screen.getByRole('button', { name: '삭제' })).toBeInTheDocument()
  })

  it('editor: test and edit, no delete', async () => {
    state.server = makeServer()
    state.permissions = ['can_use', 'can_view', 'can_edit']
    renderDetail()
    expect(await screen.findByRole('button', { name: /테스트/ })).toBeInTheDocument()
    expect(screen.getByLabelText('자격 증명')).toBeEnabled()
    expect(screen.queryByRole('button', { name: '삭제' })).not.toBeInTheDocument()
  })

  it.each([
    ['viewer', ['can_use', 'can_view']],
    ['runner', ['can_use']],
  ])('%s: test and edits are hidden', async (_level, permissions) => {
    state.server = makeServer()
    state.permissions = permissions
    renderDetail()
    await waitFor(() => expect(screen.getByLabelText('자격 증명')).toBeDisabled())
    expect(screen.queryByRole('button', { name: /테스트/ })).not.toBeInTheDocument()
    expect(screen.queryByTestId('mcp-health-check-now')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '삭제' })).not.toBeInTheDocument()
  })

  it('none: 403 offers an access request; 503 is the shared outage state', () => {
    state.server = undefined
    state.error = new ApiError(403, 'permission_required', 'x', { request_access: true })
    const { unmount } = render(
      <McpServerDetailDialog serverId="mcp-1" open onOpenChange={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: '권한 요청' })).toBeInTheDocument()
    unmount()
    state.error = new ApiError(503, 'authz_unavailable', 'x')
    renderDetail()
    expect(screen.getByText('권한을 확인하지 못했어요')).toBeInTheDocument()
  })
})

describe('MCP list cards and registration', () => {
  beforeEach(() => {
    state.error = null
    state.org = { enforced: false, capabilities: [] }
  })

  it('runner card hides health check and publish; manager sees share', async () => {
    state.server = makeServer()
    state.permissions = ['can_use']
    const { unmount } = render(<McpServersPageClient />)
    await waitFor(() => expect(screen.getByText('사내 MCP')).toBeInTheDocument())
    expect(screen.queryByTestId('check-now-mcp-1')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '게시' })).not.toBeInTheDocument()
    unmount()
    state.permissions = ['can_use', 'can_view', 'can_edit', 'can_share']
    render(<McpServersPageClient />)
    expect(await screen.findByTestId('check-now-mcp-1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /공유 설정 열기/ })).toBeInTheDocument()
  })

  it('hides new/import without cap_register_mcp when enforced', async () => {
    state.server = makeServer({ user_id: 'me' })
    state.permissions = null
    state.org = { enforced: true, capabilities: [] }
    render(<McpServersPageClient />)
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /새 MCP 서버/ })).not.toBeInTheDocument(),
    )
    expect(screen.queryByRole('button', { name: /불러오기/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /내보내기/ })).toBeInTheDocument()
  })

  it('shows new/import while capabilities are not enforced', () => {
    state.server = makeServer({ user_id: 'me' })
    render(<McpServersPageClient />)
    expect(screen.getByRole('button', { name: /새 MCP 서버/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /불러오기/ })).toBeInTheDocument()
  })
})
