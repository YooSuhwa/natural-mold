import { render, screen, userEvent, waitFor } from '../../test-utils'
import { OrganizationSettingsContent } from '@/features/organizations/components/organization-settings-content'
import { ApiError } from '@/lib/api/errors'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { makeOrgView, orgApiMock } from './org-admin-mocks'

vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { meOrganizationsApi, orgAdminApi, tenantOrganizationsApi } = orgApiMock

describe('OrganizationSettingsContent', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
  })

  it('renames with the org version and surfaces stale_version after refetching', async () => {
    orgAdminApi.get
      .mockResolvedValueOnce(makeOrgView())
      .mockResolvedValue(makeOrgView({ name: 'Alpha (by someone else)', updated_at: 'v2' }))
    orgAdminApi.update.mockRejectedValue(new ApiError(409, 'stale_version', 'stale'))
    render(<OrganizationSettingsContent />)

    const input = await screen.findByLabelText('이름')
    await userEvent.clear(input)
    await userEvent.type(input, 'Renamed')
    await userEvent.click(screen.getByRole('button', { name: '이름 저장' }))

    expect(orgAdminApi.update).toHaveBeenCalledWith(
      'org-1',
      { name: 'Renamed' },
      '2026-10-16T00:00:00Z',
    )
    await waitFor(() => expect(orgAdminApi.get).toHaveBeenCalledTimes(2))
    expect(await screen.findByDisplayValue('Alpha (by someone else)')).toBeInTheDocument()
    expect(
      screen.getByText('다른 사람이 먼저 변경했습니다. 최신 내용을 불러왔으니 다시 시도해 주세요.'),
    ).toBeInTheDocument()
  })

  it('archives only after the exact name is typed', async () => {
    orgAdminApi.get.mockResolvedValue(makeOrgView())
    tenantOrganizationsApi.archive.mockResolvedValue({})
    render(<OrganizationSettingsContent />)

    await userEvent.click(await screen.findByRole('button', { name: '조직 보관' }))
    const submit = await screen.findByRole('button', { name: '보관' })
    const confirm = screen.getByLabelText('조직 이름 입력: Alpha')
    await userEvent.type(confirm, 'alpha')
    expect(submit).toBeDisabled()
    await userEvent.clear(confirm)
    await userEvent.type(confirm, 'Alpha')
    await userEvent.click(submit)

    await waitFor(() =>
      expect(tenantOrganizationsApi.archive).toHaveBeenCalledWith(
        'tenant-1',
        'org-1',
        { confirm_name: 'Alpha' },
        '2026-10-16T00:00:00Z',
      ),
    )
  })

  it('is read-only without policy permission', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['builder'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView({ permissions: [] }))
    render(<OrganizationSettingsContent />)
    expect(await screen.findByLabelText('이름')).toBeDisabled()
    expect(screen.queryByRole('button', { name: '이름 저장' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '조직 보관' })).not.toBeInTheDocument()
  })
})
