import { render, screen, userEvent, waitFor } from '../../test-utils'
import { CompanyMembersContent } from '@/features/company-settings/components/company-members-content'
import { ApiError } from '@/lib/api/client'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from '../organizations/org-admin-mocks'
import { makeOverview, pending, tenantAdminApiMock, VERSION } from './company-mocks'

vi.mock(
  '@/lib/api/organizations',
  async () => (await import('../organizations/org-admin-mocks')).orgApiMock,
)
vi.mock('@/lib/api/tenant-admin', async () => (await import('./company-mocks')).tenantAdminApiMock)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const navigation = vi.hoisted(() => ({ search: '' }))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/settings/company/members',
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams(navigation.search),
}))

const api = tenantAdminApiMock.tenantAdminApi
const SECRET = 'company-invite-secret-xyz'

describe('CompanyMembersContent (S06)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    navigation.search = ''
    orgApiMock.meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    api.listMembers.mockResolvedValue({
      items: [
        {
          user_id: 'u-1',
          display_name: '김하나',
          email: 'hana@example.com',
          status: 'active',
          roles: [],
          created_at: '2026-10-01T00:00:00Z',
        },
      ],
      next_cursor: null,
      has_more: false,
    })
    api.listInvitations.mockResolvedValue({ items: [], next_cursor: null, has_more: false })
  })

  it('lets a company admin grant roles, suspend and invite without rendering the token', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    api.replaceRoles.mockResolvedValue({ user_id: 'u-1', roles: ['admin'] })
    api.setMemberStatus.mockResolvedValue({})
    api.createInvitation.mockResolvedValue({
      id: 'inv-1',
      email: 'new@example.com',
      role: 'auditor',
      expires_at: '2026-10-25T00:00:00Z',
      created_at: '2026-10-18T00:00:00Z',
      status: 'pending',
      token: SECRET,
    })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<CompanyMembersContent />)

    await userEvent.click(
      await screen.findByRole('button', { name: '김하나 님을 회사 관리자로 지정' }),
    )
    expect(api.replaceRoles).toHaveBeenCalledWith('tenant-1', 'u-1', ['admin'], VERSION)
    await userEvent.click(screen.getByRole('button', { name: '김하나 님 정지' }))
    expect(api.setMemberStatus).toHaveBeenCalledWith('tenant-1', 'u-1', 'suspended', VERSION)

    await userEvent.click(screen.getByRole('button', { name: '회사에 초대' }))
    await userEvent.type(await screen.findByLabelText(/이메일/), 'new@example.com')
    await userEvent.click(screen.getByRole('button', { name: '회사 감사자' }))
    await userEvent.click(screen.getByRole('button', { name: '초대 만들기' }))
    expect(api.createInvitation).toHaveBeenCalledWith(
      'tenant-1',
      { email: 'new@example.com', roles: ['auditor'] },
      VERSION,
    )
    expect(await screen.findByText('초대를 만들었습니다')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(SECRET)
    await userEvent.click(screen.getByRole('button', { name: '초대 코드 복사' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SECRET))
  })

  it('opens the invite dialog at once from the solo workspace entry (?invite=1)', async () => {
    navigation.search = 'invite=1'
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    render(<CompanyMembersContent />)
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(await screen.findByLabelText(/이메일/)).toBeInTheDocument()
  })

  it('keeps the invite dialog closed without the entry parameter', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    render(<CompanyMembersContent />)
    expect(await screen.findByRole('button', { name: '회사에 초대' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('closes the invite dialog on Escape and returns focus to its trigger', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    render(<CompanyMembersContent />)
    const trigger = await screen.findByRole('button', { name: '회사에 초대' })
    await userEvent.click(trigger)
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('confirms before removing a member from the company', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    api.removeMember.mockResolvedValue(undefined)
    render(<CompanyMembersContent />)
    await userEvent.click(await screen.findByRole('button', { name: '김하나 님을 회사에서 제거' }))
    expect(api.removeMember).not.toHaveBeenCalled()
    await userEvent.click(await screen.findByRole('button', { name: '제거' }))
    expect(api.removeMember).toHaveBeenCalledWith('tenant-1', 'u-1', VERSION)
  })

  it.each(['member', 'auditor'] as const)(
    'shows %s an admin-only notice and loads nothing',
    async (access) => {
      orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview(access))
      render(<CompanyMembersContent />)
      expect(await screen.findByText('회사 관리자만 볼 수 있습니다')).toBeInTheDocument()
      expect(api.listMembers).not.toHaveBeenCalled()
      expect(api.listInvitations).not.toHaveBeenCalled()
    },
  )

  it('renders no members while the company authority is loading', async () => {
    orgApiMock.tenantsApi.overview.mockReturnValue(pending())
    render(<CompanyMembersContent />)
    expect(await screen.findByTestId('company-loading')).toBeInTheDocument()
    expect(api.listMembers).not.toHaveBeenCalled()
  })

  it('shows the shared error state when the member list fails', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    api.listMembers.mockRejectedValue(new ApiError(503, 'authz_unavailable', 'down'))
    render(<CompanyMembersContent />)
    const alerts = await screen.findAllByRole('alert')
    expect(alerts.some((node) => node.dataset.errorKind === 'authz_unavailable')).toBe(true)
    expect(screen.queryByText('김하나')).not.toBeInTheDocument()
  })
})
