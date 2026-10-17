import { render, screen, userEvent } from '../../test-utils'
import { CompanyProfileContent } from '@/features/company-settings/components/company-profile-card'
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

const api = tenantAdminApiMock.tenantAdminApi

describe('CompanyProfileContent (S05 회사 개요)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    orgApiMock.meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
  })

  it('shows counts and lets a company admin rename the company', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    api.rename.mockResolvedValue(makeOverview('admin', { name: '한컴' }))
    render(<CompanyProfileContent />)
    expect((await screen.findAllByText('회사 관리자')).length).toBe(2)
    expect(screen.getByText('12')).toBeInTheDocument()
    const name = screen.getByLabelText('회사 이름')
    await userEvent.clear(name)
    await userEvent.type(name, '한컴')
    await userEvent.click(screen.getByRole('button', { name: '이름 저장' }))
    expect(api.rename).toHaveBeenCalledWith('tenant-1', '한컴', VERSION)
  })

  it('shows auditors counts without the rename form', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('auditor'))
    render(<CompanyProfileContent />)
    expect(await screen.findByText('회사 감사자 (읽기 전용)')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.queryByLabelText('회사 이름')).not.toBeInTheDocument()
  })

  it('shows members only the company name', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('member'))
    render(<CompanyProfileContent />)
    expect(await screen.findByText('회사 구성원')).toBeInTheDocument()
    expect(screen.queryByText('12')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '이름 저장' })).not.toBeInTheDocument()
  })

  it('renders nothing editable while loading', async () => {
    orgApiMock.tenantsApi.overview.mockReturnValue(pending())
    render(<CompanyProfileContent />)
    expect(await screen.findByTestId('company-loading')).toBeInTheDocument()
    expect(screen.queryByLabelText('회사 이름')).not.toBeInTheDocument()
  })

  it('shows the shared error state on a load failure', async () => {
    orgApiMock.tenantsApi.overview.mockRejectedValue(new ApiError(500, 'boom', 'x'))
    render(<CompanyProfileContent />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByLabelText('회사 이름')).not.toBeInTheDocument()
  })
})
