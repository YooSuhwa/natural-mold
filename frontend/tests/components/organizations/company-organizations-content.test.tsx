import { render, screen, userEvent, waitFor } from '../../test-utils'
import {
  AdministratorResult,
  CompanyOrganizationsContent,
} from '@/features/organizations/components/company-organizations-content'
import { toast } from 'sonner'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from './org-admin-mocks'

vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { meOrganizationsApi, tenantOrganizationsApi } = orgApiMock
const SECRET = 'invite-secret-token-123'

describe('CompanyOrganizationsContent', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
  })

  it('shows the auditor summary view without admin actions', async () => {
    tenantOrganizationsApi.list.mockResolvedValue({
      view: 'auditor',
      items: [{ id: 'org-1', name: 'Alpha', member_count: 4 }],
      next_cursor: null,
      has_more: false,
    })
    render(<CompanyOrganizationsContent />)
    expect(await screen.findByText('회사 감사자 보기 (요약 정보만 표시)')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '조직 만들기' })).not.toBeInTheDocument()
  })

  it('restores archived orgs and creates an org without rendering the invitation token', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const consoleSpy = vi.spyOn(console, 'log')
    tenantOrganizationsApi.list.mockResolvedValue({
      view: 'admin',
      items: [
        {
          id: 'org-9',
          name: 'Old',
          slug: 'old',
          status: 'archived',
          is_default: false,
          default_member_role: 'member',
          member_count: 0,
          administrator_user_id: null,
          updated_at: 'v3',
        },
      ],
      next_cursor: null,
      has_more: false,
    })
    tenantOrganizationsApi.restore.mockResolvedValue({})
    tenantOrganizationsApi.create.mockResolvedValue({
      organization: {
        id: 'org-10',
        name: 'Sales',
        slug: 'sales',
        status: 'active',
        is_default: false,
        default_member_role: 'member',
        member_count: 0,
        administrator_user_id: null,
        updated_at: 'v1',
      },
      administrator: {
        kind: 'invitation',
        invitation: {
          id: 'inv-1',
          email: 'boss@example.com',
          role: 'admin',
          group_ids: [],
          expires_at: '2026-10-30T00:00:00Z',
          created_at: '2026-10-16T00:00:00Z',
          status: 'pending',
          token: SECRET,
        },
      },
    })
    render(<CompanyOrganizationsContent />)

    await userEvent.click(await screen.findByRole('button', { name: '복원' }))
    expect(tenantOrganizationsApi.restore).toHaveBeenCalledWith('tenant-1', 'org-9', 'v3')

    await userEvent.click(screen.getByRole('button', { name: '조직 만들기' }))
    await userEvent.type(await screen.findByLabelText(/조직 이름/), 'Sales')
    await userEvent.type(screen.getByLabelText('관리자 이메일 (선택)'), 'boss@example.com')
    await userEvent.click(screen.getByRole('button', { name: '만들기' }))

    expect(tenantOrganizationsApi.create).toHaveBeenCalledWith('tenant-1', {
      name: 'Sales',
      default_member_role: 'member',
      administrator: { email: 'boss@example.com' },
    })
    expect(
      await screen.findByText('boss@example.com 님에게 관리자 초대를 만들었습니다.'),
    ).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(SECRET)
    await userEvent.click(screen.getByRole('button', { name: '초대 코드 복사' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SECRET))
    expect(consoleSpy.mock.calls.flat().join(' ')).not.toContain(SECRET)
  })

  it('treats an empty page by its view, not by item shape', async () => {
    tenantOrganizationsApi.list.mockResolvedValue({
      view: 'admin',
      items: [],
      next_cursor: null,
      has_more: false,
    })
    render(<CompanyOrganizationsContent />)
    expect(await screen.findByText('회사 관리자 보기')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '조직 만들기' })).toBeInTheDocument()
  })

  it('reports a copy failure when the clipboard API is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    render(
      <AdministratorResult
        administrator={{
          kind: 'invitation',
          invitation: {
            id: 'inv-2',
            email: 'x@example.com',
            role: 'admin',
            group_ids: [],
            expires_at: '2026-10-30T00:00:00Z',
            created_at: '2026-10-16T00:00:00Z',
            status: 'pending',
            token: SECRET,
          },
        }}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: '초대 코드 복사' }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('초대 코드를 복사하지 못했습니다.'),
    )
  })
})
