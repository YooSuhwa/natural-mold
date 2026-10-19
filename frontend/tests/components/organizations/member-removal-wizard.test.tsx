import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { toast } from 'sonner'
import { ApiError } from '@/lib/api/errors'
import { MemberRemovalWizard } from '@/features/organizations/components/member-removal/member-removal-wizard'
import type { MemberExitPreview, OwnedResource } from '@/lib/types/member-exit'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { trapConsole } from '../sharing/sharing-mocks'
import { orgApiMock } from './org-admin-mocks'

const memberExitMock = vi.hoisted(() => ({
  memberExitApi: { preview: vi.fn(), remove: vi.fn() },
}))
vi.mock('@/lib/api/member-exit', () => memberExitMock)
vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock('@/lib/api/grants', async () => (await import('../sharing/sharing-mocks')).grantsApiMock)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { memberExitApi } = memberExitMock
const { meOrganizationsApi, orgAdminApi } = orgApiMock

function owned(overrides: Partial<OwnedResource> & Pick<OwnedResource, 'name'>): OwnedResource {
  return {
    resource_type: 'agent',
    resource_id: `id-${overrides.name}`,
    archived: false,
    transferable: true,
    blocked_by: null,
    ...overrides,
  }
}

function preview(overrides: Partial<MemberExitPreview> = {}): MemberExitPreview {
  return {
    user_id: 'u-choi',
    status: 'active',
    is_last_admin: false,
    owned: [
      owned({ name: 'Recruiter' }),
      owned({
        name: 'Personal Gmail',
        resource_type: 'credential',
        transferable: false,
        blocked_by: { personal_account: 'google_oauth' },
      }),
    ],
    grants: [{ grant_id: 'g1', resource_type: 'agent', resource_id: 'a9', relation: 'consumer' }],
    group_ids: ['grp-1'],
    roles: ['builder'],
    trigger_ids: ['t1', 't2'],
    deployment_ids: [],
    api_key_ids: ['k1'],
    ...overrides,
  }
}

function renderWizard() {
  const onClose = vi.fn()
  render(
    <MemberRemovalWizard
      orgId="org-1"
      orgName="HR"
      member={{ userId: 'u-choi', displayName: 'Choi' }}
      version="v7"
      onClose={onClose}
    />,
  )
  return { onClose }
}

async function pickOwner(label: string) {
  const combobox = await screen.findByRole('combobox', { name: label })
  await waitFor(() => expect(combobox).toBeEnabled())
  await userEvent.type(combobox, 'Jun')
  await screen.findByRole('option', { name: /Jung Woojin/ })
  await userEvent.keyboard('{Enter}')
}

describe('MemberRemovalWizard', () => {
  let consoleTrap: ReturnType<typeof trapConsole>

  beforeEach(() => {
    vi.clearAllMocks()
    consoleTrap = trapConsole()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    orgAdminApi.searchPrincipals.mockResolvedValue([
      { type: 'user', id: 'u-jung', display_name: 'Jung Woojin', email: null, member_count: null },
    ])
  })

  afterEach(() => {
    consoleTrap.assertClean()
    consoleTrap.restore()
  })

  it('transfers owned resources, deletes the personal credential and removes the member', async () => {
    memberExitApi.preview.mockResolvedValue(preview())
    memberExitApi.remove.mockResolvedValue({
      user_id: 'u-choi',
      status: 'removed',
      transferred: ['id-Recruiter'],
      deleted: ['id-Personal Gmail'],
      revoked_grant_ids: ['g1'],
    })
    const { onClose } = renderWizard()

    const dialog = await screen.findByRole('dialog', { name: 'Choi 님을 HR에서 제거' })
    expect(await within(dialog).findByText('Recruiter')).toBeInTheDocument()
    expect(within(dialog).getByText(/개인 계정 자격 증명은 이전할 수 없어/)).toBeInTheDocument()
    const next = within(dialog).getByRole('button', { name: '다음' })
    expect(next).toBeDisabled()

    await pickOwner('Recruiter의 새 소유자')
    expect(within(dialog).getByText('새 소유자: Jung Woojin')).toBeInTheDocument()
    await userEvent.click(next)
    expect(within(dialog).getByText('소유한 스케줄 (정지)')).toBeInTheDocument()
    expect(within(dialog).getAllByText('2개').length).toBeGreaterThan(0)
    await userEvent.click(within(dialog).getByRole('button', { name: '다음' }))

    const submit = within(dialog).getByRole('button', { name: '제거' })
    expect(submit).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(/제거 사유/), 'left the company')
    await userEvent.click(submit)

    await waitFor(() =>
      expect(memberExitApi.remove).toHaveBeenCalledWith(
        'org-1',
        'u-choi',
        {
          reason: 'left the company',
          resolutions: [
            {
              resource_type: 'agent',
              resource_id: 'id-Recruiter',
              action: 'transfer',
              new_owner_id: 'u-jung',
            },
            { resource_type: 'credential', resource_id: 'id-Personal Gmail', action: 'delete' },
          ],
        },
        'v7',
      ),
    )
    expect(toast.success).toHaveBeenCalledWith('Choi 님을 조직에서 제거했어요.')
    expect(onClose).toHaveBeenCalled()
  })

  it('offers "transfer now" only for resources the caller can govern', async () => {
    memberExitApi.preview.mockResolvedValue(
      preview({
        owned: [
          owned({ name: 'Recruiter' }),
          owned({ name: 'Report bot', can_transfer_now: true }),
        ],
      }),
    )
    renderWizard()

    const dialog = await screen.findByRole('dialog', { name: 'Choi 님을 HR에서 제거' })
    expect(
      await within(dialog).findByRole('button', { name: 'Report bot 지금 바로 이전' }),
    ).toBeInTheDocument()
    expect(
      within(dialog).queryByRole('button', { name: 'Recruiter 지금 바로 이전' }),
    ).not.toBeInTheDocument()
    expect(
      within(dialog).getByText(/바로 이전하려면 이 자원을 관리할 권한이 필요해요/),
    ).toBeVisible()
  })

  it('lists blockers and keeps the removal closed while anything cannot be settled', async () => {
    memberExitApi.preview.mockResolvedValue(
      preview({
        owned: [
          owned({ name: 'Recruiter', blocked_by: { triggers: ['t-other'], api_keys: ['k1'] } }),
          owned({ name: 'Listing', resource_type: 'marketplace_item', transferable: false }),
          owned({ name: 'Old bot', archived: true }),
        ],
      }),
    )
    renderWizard()

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('다른 사람의 활성 스케줄 1개')).toBeInTheDocument()
    expect(within(dialog).getByText('폐기되지 않은 API 키 1개')).toBeInTheDocument()
    expect(within(dialog).getByText(/먼저 게시를 취소하거나 삭제하세요/)).toBeInTheDocument()
    expect(within(dialog).getByText('보관됨 · 소유자에게 남아요')).toBeInTheDocument()
    expect(within(dialog).getByRole('alert')).toHaveTextContent('먼저 정리해야 할 자원이 있어')
    expect(within(dialog).getByRole('button', { name: '다음' })).toBeDisabled()
  })

  it('blocks removing the last organization admin', async () => {
    memberExitApi.preview.mockResolvedValue(preview({ owned: [], is_last_admin: true }))
    renderWizard()
    expect(await screen.findByText(/마지막 조직 관리자는 제거할 수 없어요/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '다음' })).toBeDisabled()
  })

  it('fails closed with the shared error state when the preview is denied', async () => {
    memberExitApi.preview.mockRejectedValue(
      new ApiError(403, 'permission_required', 'denied', { required: 'can_manage_members' }),
    )
    renderWizard()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveAttribute('data-error-kind', 'permission_required')
    expect(screen.queryByRole('button', { name: '다음' })).toBeDisabled()
    expect(memberExitApi.remove).not.toHaveBeenCalled()
  })

  it('maps a stale version to a localized message and re-reads the preview', async () => {
    memberExitApi.preview.mockResolvedValue(preview({ owned: [] }))
    memberExitApi.remove.mockRejectedValue(new ApiError(409, 'stale_version', 'stale'))
    renderWizard()
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(await within(dialog).findByRole('button', { name: '다음' }))
    await userEvent.click(within(dialog).getByRole('button', { name: '다음' }))
    await userEvent.type(within(dialog).getByLabelText(/제거 사유/), 'policy')
    await userEvent.click(within(dialog).getByRole('button', { name: '제거' }))
    expect(await within(dialog).findByText(/다른 사람이 먼저 변경했어요/)).toBeInTheDocument()
    await waitFor(() => expect(memberExitApi.preview.mock.calls.length).toBeGreaterThanOrEqual(2))
  })
})
