import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { toast } from 'sonner'
import { ApiError } from '@/lib/api/errors'
import { TransferOwnershipDialog } from '@/components/sharing/transfer-ownership-dialog'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from '../organizations/org-admin-mocks'
import { accessList, grantsApiMock, trapConsole } from './sharing-mocks'

vi.mock('@/lib/api/grants', async () => (await import('./sharing-mocks')).grantsApiMock)
vi.mock(
  '@/lib/api/organizations',
  async () => (await import('../organizations/org-admin-mocks')).orgApiMock,
)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { grantsApi } = grantsApiMock
const { meOrganizationsApi, orgAdminApi } = orgApiMock

function renderDialog(isOwner = false) {
  const onOpenChange = vi.fn()
  const onTransferred = vi.fn()
  render(
    <TransferOwnershipDialog
      open
      onOpenChange={onOpenChange}
      resourceType="agent"
      resourceId="agent-1"
      resourceName="Recruiter"
      ownerId="user-me"
      isOwner={isOwner}
      onTransferred={onTransferred}
    />,
  )
  return { onOpenChange, onTransferred }
}

async function pickOwner() {
  const combobox = await screen.findByRole('combobox', { name: '새 소유자' })
  await waitFor(() => expect(combobox).toBeEnabled())
  await userEvent.type(combobox, 'Jun')
  await screen.findByRole('option', { name: /Jung/ })
  await userEvent.keyboard('{Enter}')
}

describe('TransferOwnershipDialog', () => {
  let consoleTrap: ReturnType<typeof trapConsole>

  beforeEach(() => {
    vi.clearAllMocks()
    consoleTrap = trapConsole()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    grantsApi.access.mockResolvedValue(accessList())
    orgAdminApi.searchPrincipals.mockResolvedValue([
      { type: 'user', id: 'user-jung', display_name: 'Jung', email: null, member_count: null },
    ])
  })

  afterEach(() => {
    consoleTrap.assertClean()
    consoleTrap.restore()
  })

  it('requires a governance reason and transfers after confirmation', async () => {
    grantsApi.transfer.mockResolvedValue({})
    const { onOpenChange, onTransferred } = renderDialog()
    const dialog = await screen.findByRole('dialog', { name: '소유권 이전' })
    await pickOwner()
    const submit = within(dialog).getByRole('button', { name: '소유권 이전' })
    expect(submit).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText('사유 (필수)'), 'member left')
    await userEvent.click(submit)
    await userEvent.click(await screen.findByRole('button', { name: '소유권 이전' }))
    await waitFor(() =>
      expect(grantsApi.transfer).toHaveBeenCalledWith('agent', 'agent-1', {
        new_owner_id: 'user-jung',
        reason: 'member left',
        keep_manager: false,
      }),
    )
    expect(toast.success).toHaveBeenCalledWith('Jung님에게 소유권을 넘겼어요.')
    expect(onTransferred).toHaveBeenCalled()
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('lists the dependents that block the transfer', async () => {
    grantsApi.transfer.mockRejectedValue(
      new ApiError(409, 'transfer_blocked', 'blocked', { triggers: ['t1', 't2'], api_keys: ['k'] }),
    )
    const { onOpenChange } = renderDialog(true)
    const dialog = await screen.findByRole('dialog', { name: '소유권 이전' })
    await pickOwner()
    await userEvent.click(within(dialog).getByRole('button', { name: '소유권 이전' }))
    await userEvent.click(await screen.findByRole('button', { name: '소유권 이전' }))
    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent('실행 중인 스케줄 2개')
    expect(alert).toHaveTextContent('폐기되지 않은 API 키 1개')
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
  })

  it('fails closed without can_govern', async () => {
    grantsApi.access.mockResolvedValue(accessList({ my_permissions: ['can_share'] }))
    renderDialog()
    expect(await screen.findByText('이 자원의 소유권을 이전할 권한이 없어요.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '소유권 이전' })).toBeDisabled()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })
})
