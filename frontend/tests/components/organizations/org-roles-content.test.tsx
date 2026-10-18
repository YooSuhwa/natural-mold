import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { OrgRolesContent } from '@/features/organizations/components/org-roles-content'
import type {
  DirectoryPage,
  GrantDirectoryQuery,
  OrgRole,
  RoleGrantView,
  RolePrincipal,
} from '@/lib/types/organization'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { makeOrgView, orgApiMock } from './org-admin-mocks'

vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { meOrganizationsApi, orgAdminApi } = orgApiMock
const G1: RolePrincipal = { type: 'group', id: 'g1' }
const U2: RolePrincipal = { type: 'user', id: 'u2' }

function grant(
  principal: RolePrincipal,
  role: OrgRole,
  id = `${principal.id}-${role}`,
): RoleGrantView {
  return { id, principal, role, granted_by: null, granted_at: '2026-10-01T00:00:00Z' }
}

function page(items: RoleGrantView[], next: string | null = null): DirectoryPage<RoleGrantView> {
  return { items, next_cursor: next, has_more: next !== null, updated_at: 'v5' }
}

/** Server state per principal; the filtered endpoint pages it one grant at a time. */
let grantsByPrincipal: Record<string, OrgRole[]>
let unfilteredPage: DirectoryPage<RoleGrantView>

function installRoleServer() {
  orgAdminApi.listRoles.mockImplementation(async (_org: string, query: GrantDirectoryQuery) => {
    if (!query.principal) return unfilteredPage
    if (query.principal.type === 'organization') return page([])
    const principal: RolePrincipal = query.principal
    const roles = grantsByPrincipal[`${principal.type}:${principal.id}`] ?? []
    const index = query.cursor ? Number(query.cursor) : 0
    const current = roles[index]
    const items = current ? [grant(principal, current)] : []
    return page(items, index + 1 < roles.length ? String(index + 1) : null)
  })
}

function groupFor(name: RegExp) {
  return screen.findByRole('group', { name })
}

describe('OrgRolesContent', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['admin'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView())
    orgAdminApi.listCapabilities.mockResolvedValue({
      items: [],
      next_cursor: null,
      has_more: false,
      updated_at: 'v5',
    })
    orgAdminApi.replaceRoles.mockResolvedValue(page([]))
    grantsByPrincipal = {}
    unfilteredPage = page([])
    installRoleServer()
  })

  it('names listed principals by the server display name, not an id prefix', async () => {
    grantsByPrincipal = { 'user:u2': ['admin'], 'group:g1': ['builder'] }
    unfilteredPage = page([
      { ...grant(U2, 'admin'), principal_name: '김지원' },
      { ...grant(G1, 'builder'), principal_name: '플랫폼팀' },
    ])
    render(<OrgRolesContent />)

    expect(await groupFor(/김지원 \(사용자\) 권한/)).toBeInTheDocument()
    expect(await groupFor(/플랫폼팀 \(그룹\) 권한/)).toBeInTheDocument()
    expect(screen.queryByText('u2')).not.toBeInTheDocument()
  })

  it('keeps grants that live on another list page when saving', async () => {
    // Given: g1 holds builder (page 1) and auditor (only visible on page 2).
    grantsByPrincipal = { 'group:g1': ['builder', 'auditor'] }
    unfilteredPage = page([grant(G1, 'builder')], 'next-page')
    render(<OrgRolesContent />)

    // When: the admin adds "admin" for g1 and saves.
    const row = await groupFor(/g1 \(그룹\) 권한/)
    await waitFor(() => expect(within(row).getByRole('checkbox', { name: '감사자' })).toBeChecked())
    await userEvent.click(within(row).getByRole('checkbox', { name: '관리자' }))
    await userEvent.click(within(row).getByRole('button', { name: 'g1 저장' }))

    // Then: the replace payload still carries the page-2 auditor grant.
    await waitFor(() =>
      expect(orgAdminApi.replaceRoles).toHaveBeenCalledWith(
        'org-1',
        { principal: G1, roles: ['builder', 'auditor', 'admin'] },
        'v5',
      ),
    )
  })

  it('seeds a picker-added principal from its existing grants', async () => {
    grantsByPrincipal = { 'user:u2': ['admin'] }
    orgAdminApi.searchPrincipals.mockResolvedValue([
      { type: 'user', id: 'u2', display_name: 'Lee', email: 'lee@example.com', member_count: null },
    ])
    render(<OrgRolesContent />)

    const [rolePicker] = await screen.findAllByLabelText('이름 또는 이메일로 검색')
    await userEvent.type(rolePicker, 'Lee')
    await userEvent.click((await screen.findAllByRole('button', { name: '선택' }))[0])
    const row = await groupFor(/Lee \(사용자\) 권한/)
    await waitFor(() => expect(within(row).getByRole('checkbox', { name: '관리자' })).toBeChecked())
    await userEvent.click(within(row).getByRole('checkbox', { name: '빌더' }))
    await userEvent.click(within(row).getByRole('button', { name: 'Lee 저장' }))

    await waitFor(() =>
      expect(orgAdminApi.replaceRoles).toHaveBeenCalledWith(
        'org-1',
        { principal: U2, roles: ['admin', 'builder'] },
        'v5',
      ),
    )
  })

  it('does not clobber an unsaved edit in one row when another row is saved', async () => {
    grantsByPrincipal = { 'group:g1': ['builder'], 'user:u2': ['auditor'] }
    unfilteredPage = page([grant(G1, 'builder'), grant(U2, 'auditor')])
    render(<OrgRolesContent />)

    const g1 = await groupFor(/g1 \(그룹\) 권한/)
    const u2 = await groupFor(/u2 \(사용자\) 권한/)
    await waitFor(() => expect(within(u2).getByRole('checkbox', { name: '감사자' })).toBeChecked())
    await userEvent.click(within(g1).getByRole('checkbox', { name: '감사자' }))

    grantsByPrincipal = { 'group:g1': ['builder'], 'user:u2': ['builder'] }
    await userEvent.click(within(u2).getByRole('checkbox', { name: '빌더' }))
    await userEvent.click(within(u2).getByRole('checkbox', { name: '감사자' }))
    await userEvent.click(within(u2).getByRole('button', { name: 'u2 저장' }))

    await waitFor(() => expect(within(u2).getByRole('checkbox', { name: '빌더' })).toBeChecked())
    expect(within(g1).getByRole('checkbox', { name: '감사자' })).toBeChecked()
    expect(within(g1).getByRole('button', { name: 'g1 저장' })).toBeEnabled()
  })

  it('lets company admins who are plain members manage grants via server permissions', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: [] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView({ permissions: ['can_manage_members'] }))
    unfilteredPage = page([grant(G1, 'builder')])
    grantsByPrincipal = { 'group:g1': ['builder'] }
    render(<OrgRolesContent />)
    expect(await groupFor(/g1 \(그룹\) 권한/)).toBeInTheDocument()
  })

  it('hides grants from non-managers without calling the list API', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['auditor'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView({ permissions: [] }))
    render(<OrgRolesContent />)
    expect(await screen.findByText('조직 관리자만 볼 수 있습니다.')).toBeInTheDocument()
    expect(orgAdminApi.listRoles).not.toHaveBeenCalled()
  })

  it('shows the fixed role defaults matrix', async () => {
    render(<OrgRolesContent />)
    const table = await screen.findByRole('table')
    const row = within(table).getByRole('row', { name: /데이터베이스 연결/ })
    const cells = within(row).getAllByRole('cell')
    // feature, admin, builder, member, auditor
    expect(cells[1]).toHaveTextContent('기본 포함')
    expect(cells[2]).toHaveTextContent('기본 미포함')
    const activity = within(table).getByRole('row', { name: /활동 기록·사용량 보기/ })
    expect(within(activity).getAllByRole('cell')[4]).toHaveTextContent('기본 포함')
  })
})
