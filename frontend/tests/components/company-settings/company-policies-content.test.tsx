import { render, screen, userEvent, waitFor } from '../../test-utils'
import { CompanyPoliciesContent } from '@/features/company-settings/components/company-policies-content'
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
const policies = (overrides = {}) => ({
  marketplace_tenant_publish: 'approval',
  audit_retention_days: 365,
  updated_at: VERSION,
  ...overrides,
})

describe('CompanyPoliciesContent (S05 정책·공개 승인)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    orgApiMock.meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    api.getPolicies.mockResolvedValue(policies())
    api.listPublicationRequests.mockResolvedValue({
      items: [
        {
          item_id: 'item-1',
          name: '보고서 스킬',
          description: '주간 보고서',
          resource_type: 'skill',
          organization: { id: 'org-2', name: '영업팀' },
          publisher: { id: 'u-2', name: '박두리' },
          requested_at: '2026-10-17T00:00:00Z',
          credential_types: ['openai'],
        },
      ],
      next_cursor: null,
      has_more: false,
    })
  })

  it('lets a company admin change policies, confirm a shortened retention and decide requests', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    api.putPolicies
      .mockResolvedValueOnce({ ...policies(), preview: { audit_events: 42 } })
      .mockResolvedValueOnce(policies({ audit_retention_days: 90 }))
    api.approvePublication.mockResolvedValue({})
    render(<CompanyPoliciesContent />)

    const retention = await screen.findByLabelText('감사 기록 보존 기간 (일)')
    await userEvent.clear(retention)
    await userEvent.type(retention, '90')
    await userEvent.click(screen.getByRole('button', { name: '정책 저장' }))
    expect(api.putPolicies).toHaveBeenCalledWith(
      'tenant-1',
      { marketplace_tenant_publish: 'approval', audit_retention_days: 90, confirm: false },
      VERSION,
    )
    expect(await screen.findByText(/감사 기록 42건/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '줄이고 저장' }))
    await waitFor(() =>
      expect(api.putPolicies).toHaveBeenLastCalledWith(
        'tenant-1',
        { marketplace_tenant_publish: 'approval', audit_retention_days: 90, confirm: true },
        VERSION,
      ),
    )

    expect(await screen.findByText('보고서 스킬')).toBeInTheDocument()
    expect(screen.getByText('필요한 자격증명 유형: openai')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '보고서 스킬 승인' }))
    expect(api.approvePublication).toHaveBeenCalledWith('tenant-1', 'item-1')
  })

  it('requires a reason to reject a publication request', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    api.rejectPublication.mockResolvedValue({})
    render(<CompanyPoliciesContent />)
    await userEvent.click(await screen.findByRole('button', { name: '보고서 스킬 거절' }))
    const submit = screen.getByRole('button', { name: '거절하기' })
    expect(submit).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/거절 사유/), '민감 정보 포함')
    await userEvent.click(submit)
    expect(api.rejectPublication).toHaveBeenCalledWith('tenant-1', 'item-1', '민감 정보 포함')
  })

  it('shows company members a read-only view without the approval queue', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('member'))
    render(<CompanyPoliciesContent />)
    expect(await screen.findByText(/회사 구성원은 정책을 볼 수만 있습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '승인' })).toBeDisabled()
    expect(screen.getByLabelText('감사 기록 보존 기간 (일)')).toBeDisabled()
    expect(screen.queryByRole('button', { name: '정책 저장' })).not.toBeInTheDocument()
    expect(screen.queryByText('공개 승인')).not.toBeInTheDocument()
    expect(api.listPublicationRequests).not.toHaveBeenCalled()
  })

  it('renders no controls while the company authority is loading', async () => {
    orgApiMock.tenantsApi.overview.mockReturnValue(pending())
    render(<CompanyPoliciesContent />)
    expect(await screen.findByTestId('company-loading')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '정책 저장' })).not.toBeInTheDocument()
    expect(api.getPolicies).not.toHaveBeenCalled()
  })

  it('shows the shared error state when the company cannot be loaded', async () => {
    orgApiMock.tenantsApi.overview.mockRejectedValue(new ApiError(404, 'not_found', 'missing'))
    render(<CompanyPoliciesContent />)
    expect(await screen.findByRole('alert')).toHaveAttribute('data-error-kind', 'not_found')
    expect(screen.queryByRole('button', { name: '정책 저장' })).not.toBeInTheDocument()
  })
})
