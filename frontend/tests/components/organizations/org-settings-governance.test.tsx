import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { OrganizationSettingsContent } from '@/features/organizations/components/organization-settings-content'
import { ApiError } from '@/lib/api/errors'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import {
  governanceApiMock,
  makeLimitView,
  makeOrgView,
  orgApiMock,
  resetGovernanceMocks,
} from './org-admin-mocks'

vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock(
  '@/lib/api/org-governance',
  async () => (await import('./org-admin-mocks')).governanceApiMock,
)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { meOrganizationsApi, orgAdminApi } = orgApiMock
const api = governanceApiMock.orgGovernanceApi
const VERSION = '2026-10-16T00:00:00Z'

describe('OrganizationSettingsContent governance sections (S08)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetGovernanceMocks()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['admin'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView())
  })

  it('saves the public-link policy with the organization version', async () => {
    orgAdminApi.update.mockResolvedValue(makeOrgView())
    render(<OrganizationSettingsContent />)

    await userEvent.click(await screen.findByRole('switch', { name: '대화 공개 링크 허용' }))
    await userEvent.click(screen.getByRole('button', { name: '정책 저장' }))

    expect(orgAdminApi.update).toHaveBeenCalledWith(
      'org-1',
      {
        settings: {
          default_member_role: 'member',
          registration_policy: null,
          allow_public_share_links: false,
        },
      },
      VERSION,
    )
  })

  it('previews deletions before a shorter retention is saved, then confirms', async () => {
    api.putRetention
      .mockResolvedValueOnce({
        saved: false,
        preview: {
          conversations: 1240,
          agents: 0,
          removed_member_conversations: 0,
          removed_member_memories: 0,
          audit_events: 0,
        },
        policy: {
          conversation_days: null,
          agent_trash_days: 30,
          removed_member_days: 30,
          updated_at: VERSION,
        },
      })
      .mockResolvedValueOnce({
        saved: true,
        preview: null,
        policy: {
          conversation_days: 90,
          agent_trash_days: 30,
          removed_member_days: 30,
          updated_at: 'v2',
        },
      })
    render(<OrganizationSettingsContent />)

    await userEvent.click(await screen.findByRole('checkbox', { name: '대화 기록 무기한 보존' }))
    const days = screen.getByLabelText('대화 기록 보존(일)')
    await userEvent.clear(days)
    await userEvent.type(days, '90')
    await userEvent.click(screen.getByRole('button', { name: '보존 정책 저장' }))

    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText(/대화 1240건/)).toBeInTheDocument()
    expect(api.putRetention).toHaveBeenLastCalledWith(
      'org-1',
      { conversation_days: 90, agent_trash_days: 30, removed_member_days: 30, confirm: false },
      VERSION,
    )
    await userEvent.click(within(dialog).getByRole('button', { name: '줄이고 저장' }))
    await waitFor(() =>
      expect(api.putRetention).toHaveBeenLastCalledWith(
        'org-1',
        { conversation_days: 90, agent_trash_days: 30, removed_member_days: 30, confirm: true },
        VERSION,
      ),
    )
  })

  it('blocks out-of-range retention days', async () => {
    render(<OrganizationSettingsContent />)
    const trash = await screen.findByLabelText('에이전트 휴지통 기간(일)')
    await userEvent.clear(trash)
    await userEvent.type(trash, '120')
    expect(screen.getByText('허용 범위를 벗어났습니다.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '보존 정책 저장' })).toBeDisabled()
  })

  it('shows org usage and saves the member default limit under the org limit', async () => {
    api.putMemberDefault.mockResolvedValue(makeLimitView({ default_member_limit: '20' }))
    render(<OrganizationSettingsContent />)

    expect(await screen.findByText('이번 달 사용 $12.50 / 한도 $100.00')).toBeInTheDocument()
    const input = screen.getByLabelText('구성원 기본 월 한도(USD)')
    await userEvent.type(input, '200')
    expect(screen.getByText('조직 한도보다 클 수 없어요.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '기본 한도 저장' })).toBeDisabled()
    await userEvent.clear(input)
    await userEvent.type(input, '20')
    await userEvent.click(screen.getByRole('button', { name: '기본 한도 저장' }))
    expect(api.putMemberDefault).toHaveBeenCalledWith('org-1', '20', VERSION)
  })

  it('is read-only for members and hides usage limits (fail closed)', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['builder'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView({ permissions: [] }))
    render(<OrganizationSettingsContent />)

    expect(await screen.findByRole('switch', { name: '대화 공개 링크 허용' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(screen.queryByRole('button', { name: '정책 저장' })).not.toBeInTheDocument()
    expect(await screen.findByLabelText('에이전트 휴지통 기간(일)')).toBeDisabled()
    expect(screen.queryByText('사용 한도')).not.toBeInTheDocument()
    expect(api.getLimits).not.toHaveBeenCalled()
  })

  it('renders the shared error state when a section fails to load', async () => {
    api.getRetention.mockRejectedValue(new ApiError(503, 'authz_unavailable', 'down'))
    render(<OrganizationSettingsContent />)
    expect(await screen.findByRole('alert')).toHaveAttribute('data-error-kind', 'authz_unavailable')
  })
})
