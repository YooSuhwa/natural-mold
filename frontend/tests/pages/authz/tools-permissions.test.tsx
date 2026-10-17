import { render, screen, waitFor } from '../../test-utils'
import { ToolDetailDialog } from '@/app/tools/_components/tool-detail-dialog'
import { ToolsPageClient } from '@/app/tools/_components/tools-page-client'
import { ApiError } from '@/lib/api/errors'
import type { ToolInstance } from '@/lib/types/tool'

/** Plan 13.9 row 7 — tool catalog/detail/create/test-run: list can_use, test can_edit,
 * create cap_create_tool. */
const state = vi.hoisted(() => ({
  tool: null as unknown,
  error: null as unknown,
  permissions: null as string[] | null,
  org: { enforced: false, capabilities: [] as string[] },
}))

vi.mock('@/lib/hooks/use-tools', () => ({
  useTool: () => ({ data: state.tool, isLoading: false, error: state.error }),
  useTools: () => ({ data: [], isLoading: false }),
  useToolType: () => ({ data: undefined }),
  useToolTypes: () => ({
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
  }),
  useUpdateTool: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteTool: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRunTool: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-credentials', () => ({ useCredentials: () => ({ data: [] }) }))
vi.mock('@/app/tools/_components/tool-create-dialog', () => ({ ToolCreateDialog: () => null }))
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
        name: 'Org',
        is_active: true,
        roles: [],
        capabilities: state.org.capabilities,
        capabilities_enforced: state.org.enforced,
      },
    ]),
  },
}))

function makeTool(overrides: Partial<ToolInstance> = {}): ToolInstance {
  return {
    id: 'tool-1',
    user_id: 'other',
    definition_key: 'web_search',
    name: '검색 도구',
    description: null,
    parameters: {},
    credential_id: null,
    enabled: true,
    last_used_at: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

function renderDetail() {
  render(<ToolDetailDialog toolId="tool-1" open onOpenChange={vi.fn()} />)
}

describe('tool detail by permission level', () => {
  beforeEach(() => {
    state.error = null
    state.permissions = null
  })

  it('owner (legacy body): test run, delete and share', async () => {
    state.tool = makeTool({ user_id: 'me' })
    renderDetail()
    expect(screen.getByRole('button', { name: '실행' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '삭제' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /공유 설정 열기/ })).toBeInTheDocument()
  })

  it('editor: test run without delete or share', async () => {
    state.tool = makeTool()
    state.permissions = ['can_use', 'can_view', 'can_edit']
    renderDetail()
    expect(await screen.findByRole('button', { name: '실행' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '삭제' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /공유 설정 열기/ })).not.toBeInTheDocument()
  })

  it.each([
    ['viewer', ['can_use', 'can_view']],
    ['runner', ['can_use']],
  ])('%s: read only, no test run', async (_level, permissions) => {
    state.tool = makeTool()
    state.permissions = permissions
    renderDetail()
    await waitFor(() => expect(screen.getByText(/보기 전용이에요/)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: '실행' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '삭제' })).not.toBeInTheDocument()
  })

  it('manager on a shared tool sees the share entry (my_permissions)', async () => {
    state.tool = makeTool()
    state.permissions = ['can_use', 'can_view', 'can_edit', 'can_share']
    renderDetail()
    expect(await screen.findByRole('button', { name: /공유 설정 열기/ })).toBeInTheDocument()
  })

  it('none: 403 offers an access request, 404 is not found', () => {
    state.tool = undefined
    state.error = new ApiError(403, 'permission_required', 'x', { request_access: true })
    const { unmount } = render(<ToolDetailDialog toolId="tool-1" open onOpenChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: '권한 요청' })).toBeInTheDocument()
    unmount()
    state.error = new ApiError(404, 'TOOL_NOT_FOUND', 'x')
    renderDetail()
    expect(screen.getByText('찾을 수 없어요')).toBeInTheDocument()
  })
})

describe('tool creation capability', () => {
  it('shows the definition catalog while capabilities are not enforced', async () => {
    state.org = { enforced: false, capabilities: [] }
    render(<ToolsPageClient />)
    expect(await screen.findByRole('tab', { name: /검색/ })).toBeInTheDocument()
  })

  it('hides the catalog (creation) without cap_create_tool when enforced', async () => {
    state.org = { enforced: true, capabilities: [] }
    render(<ToolsPageClient />)
    await waitFor(() => expect(screen.queryByRole('tab', { name: /검색/ })).not.toBeInTheDocument())
    expect(screen.getByRole('tab', { name: /설치됨/ })).toBeInTheDocument()
  })
})
