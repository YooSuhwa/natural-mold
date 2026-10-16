import { QueryClient } from '@tanstack/react-query'
import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { toast } from 'sonner'
import { ApiError } from '@/lib/api/errors'
import { ResourceShareDialog } from '@/components/sharing/resource-share-dialog'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from '../organizations/org-admin-mocks'
import { accessList, entry, grantsApiMock, trapConsole } from './sharing-mocks'

vi.mock('@/lib/api/grants', async () => (await import('./sharing-mocks')).grantsApiMock)
vi.mock(
  '@/lib/api/organizations',
  async () => (await import('../organizations/org-admin-mocks')).orgApiMock,
)
vi.mock('@/lib/auth/session', () => ({ useSession: () => ({ data: { id: 'user-me' } }) }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { grantsApi } = grantsApiMock
const { meOrganizationsApi, orgAdminApi } = orgApiMock

function renderDialog(props: Partial<Parameters<typeof ResourceShareDialog>[0]> = {}) {
  const onOpenChange = vi.fn()
  render(
    <ResourceShareDialog
      open
      onOpenChange={onOpenChange}
      resourceType="agent"
      resourceId="agent-1"
      resourceName="Helper"
      {...props}
    />,
  )
  return { onOpenChange }
}

async function pickRecipient(name: string) {
  const combobox = await screen.findByRole('combobox', { name: '공유할 사람 또는 그룹' })
  await waitFor(() => expect(combobox).toBeEnabled())
  await userEvent.type(combobox, name.slice(0, 3))
  await screen.findByRole('option', { name: new RegExp(name) })
  await userEvent.keyboard('{ArrowDown}{ArrowUp}{Enter}')
}

describe('ResourceShareDialog', () => {
  let consoleTrap: ReturnType<typeof trapConsole>

  beforeEach(() => {
    vi.clearAllMocks()
    consoleTrap = trapConsole()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    grantsApi.access.mockResolvedValue(accessList())
    orgAdminApi.searchPrincipals.mockResolvedValue([
      { type: 'user', id: 'user-lee', display_name: 'Lee Park', email: null, member_count: null },
    ])
  })

  afterEach(() => {
    // No React/Base UI warnings (missing title/description, act, keys, props).
    consoleTrap.assertClean()
    consoleTrap.restore()
  })

  it('renders a labelled dialog with the access list and inherited rows', async () => {
    renderDialog()
    const dialog = await screen.findByRole('dialog', { name: 'Helper 공유' })
    expect(dialog).toHaveAccessibleDescription(/같은 조직의 사용자/)
    const list = await within(dialog).findByRole('list', { name: '접근 권한이 있는 대상' })
    expect(within(list).getByText('Me')).toBeInTheDocument()
    expect(within(list).getByText('소유자')).toBeInTheDocument()
    expect(within(list).getByText('그룹 · 4명')).toBeInTheDocument()
    // Owner row is never revocable; direct and group grants are.
    expect(within(list).getAllByRole('button', { name: /공유 회수/ })).toHaveLength(2)
    expect(grantsApi.access).toHaveBeenCalledWith('agent', 'agent-1')
  })

  it('offers only the levels the resource type allows', async () => {
    renderDialog({ resourceType: 'credential' })
    const group = await screen.findByRole('group', { name: '권한 단계' })
    const radios = within(group).getAllByRole('radio')
    expect(radios.map((radio) => (radio as HTMLInputElement).value)).toEqual([
      'consumer',
      'manager',
    ])
    expect(screen.getByRole('note')).toHaveTextContent('비밀 값은 누구에게도 보이지 않아요')
  })

  it('adds a recipient with the chosen level and expiry through the keyboard', async () => {
    grantsApi.create.mockResolvedValue({})
    renderDialog()
    await pickRecipient('Lee Park')
    expect(screen.getByText('Lee Park')).toBeInTheDocument()
    expect(orgAdminApi.searchPrincipals).toHaveBeenCalledWith('org-1', 'Lee', ['user', 'group'])

    await userEvent.click(screen.getByRole('radio', { name: /^실행/ }))
    await userEvent.type(screen.getByLabelText('만료일 (선택)'), '2099-01-31')
    await userEvent.click(screen.getByRole('button', { name: '공유' }))

    await waitFor(() => expect(grantsApi.create).toHaveBeenCalledTimes(1))
    const [type, id, body] = grantsApi.create.mock.calls[0]
    expect([type, id]).toEqual(['agent', 'agent-1'])
    expect(body).toMatchObject({
      principal: { type: 'user', id: 'user-lee' },
      level: 'consumer',
      confirm_delegation: false,
    })
    expect(body.expires_at).toMatch(/^2099-01-31T|^2099-02-01T/)
    expect(toast.success).toHaveBeenCalledWith('Lee Park에게 공유했어요.')
    await waitFor(() => expect(grantsApi.access).toHaveBeenCalledTimes(2))
  })

  it('hides expiry for levels other than agent run', async () => {
    renderDialog()
    await userEvent.click(await screen.findByRole('radio', { name: /^편집/ }))
    expect(screen.queryByLabelText('만료일 (선택)')).not.toBeInTheDocument()
  })

  it('asks to confirm delegated accounts on confirm_required and resubmits', async () => {
    grantsApi.create
      .mockRejectedValueOnce(
        new ApiError(409, 'confirm_required', 'confirm', {
          delegations: [{ resource_type: 'credential', resource_id: 'cred-1', name: 'Gmail' }],
        }),
      )
      .mockResolvedValueOnce({})
    renderDialog()
    await pickRecipient('Lee Park')
    await userEvent.click(screen.getByRole('button', { name: '공유' }))

    const confirm = await screen.findByRole('group', { name: '위임된 계정 자원을 확인하세요' })
    expect(within(confirm).getByText('Gmail')).toBeInTheDocument()
    expect(within(confirm).getByRole('button', { name: '취소' })).toHaveFocus()
    expect(toast.error).not.toHaveBeenCalled()

    await userEvent.click(within(confirm).getByRole('button', { name: '확인하고 공유' }))
    await waitFor(() => expect(grantsApi.create).toHaveBeenCalledTimes(2))
    expect(grantsApi.create.mock.calls[1][2]).toMatchObject({ confirm_delegation: true })
  })

  it('revokes after a confirmation naming the target', async () => {
    grantsApi.revoke.mockResolvedValue(undefined)
    renderDialog()
    await userEvent.click(await screen.findByRole('button', { name: 'Team 공유 회수' }))
    const alert = await screen.findByRole('alertdialog')
    expect(alert).toHaveTextContent('Team의 공유를 회수할까요?')
    expect(alert).toHaveTextContent('그룹 구성원 4명')
    await userEvent.click(within(alert).getByRole('button', { name: '회수' }))
    await waitFor(() => expect(grantsApi.revoke).toHaveBeenCalledWith('grant-team'))
    expect(toast.success).toHaveBeenCalledWith('Team의 공유를 회수했어요.')
  })

  it('maps grant_invalid reasons to messages', async () => {
    grantsApi.create.mockRejectedValue(
      new ApiError(422, 'grant_invalid', 'invalid', { reason: 'self_grant' }),
    )
    renderDialog()
    await pickRecipient('Lee Park')
    await userEvent.click(screen.getByRole('button', { name: '공유' }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('내 권한은 낮추기만 할 수 있어요.'),
    )
  })

  it('is read-only without can_share and shows the load error with retry', async () => {
    grantsApi.access.mockResolvedValueOnce(accessList({ my_permissions: ['can_revoke'] }))
    renderDialog()
    expect(await screen.findByText(/공유 권한이 없어/)).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '소유권 이전' })).not.toBeInTheDocument()
  })

  it('shows a permission error state when /access is forbidden', async () => {
    grantsApi.access.mockRejectedValue(new ApiError(403, 'permission_required', 'no'))
    renderDialog()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('권한이 없어요.')
    await userEvent.click(within(alert).getByRole('button', { name: '다시 시도' }))
    await waitFor(() => expect(grantsApi.access).toHaveBeenCalledTimes(2))
  })

  it('requires acknowledging the owner account for shared-account tools', async () => {
    renderDialog({ resourceType: 'tool', sharedAccount: true })
    await pickRecipient('Lee Park')
    const submit = screen.getByRole('button', { name: '공유' })
    expect(submit).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: /소유자 계정으로 호출된다는/ }))
    expect(submit).toBeEnabled()
  })

  it('transfers ownership and lists transfer_blocked dependents', async () => {
    grantsApi.transfer.mockRejectedValue(
      new ApiError(409, 'transfer_blocked', 'blocked', { triggers: ['t1', 't2'] }),
    )
    renderDialog()
    const toggle = await screen.findByRole('button', { name: '소유권 이전' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    const combobox = screen.getByRole('combobox', { name: '새 소유자' })
    await userEvent.type(combobox, 'Lee')
    await userEvent.click(await screen.findByRole('option', { name: /Lee Park/ }))
    const buttons = screen.getAllByRole('button', { name: '소유권 이전' })
    await userEvent.click(buttons[buttons.length - 1])
    const alert = await screen.findByRole('alertdialog')
    expect(alert).toHaveTextContent('Helper의 소유권을 Lee Park님에게 넘길까요?')
    await userEvent.click(within(alert).getByRole('button', { name: '소유권 이전' }))

    await waitFor(() =>
      expect(grantsApi.transfer).toHaveBeenCalledWith('agent', 'agent-1', {
        new_owner_id: 'user-lee',
        reason: null,
        keep_manager: true,
      }),
    )
    expect(await screen.findByText('실행 중인 스케줄 2개')).toBeInTheDocument()
    expect(toast.error).toHaveBeenCalledWith('아직 소유권을 이전할 수 없어요.')
  })

  it('closes on Escape', async () => {
    const { onOpenChange } = renderDialog()
    await screen.findByRole('dialog')
    await userEvent.keyboard('{Escape}')
    expect(onOpenChange).toHaveBeenCalledWith(false, expect.anything())
  })

  it('closes the dialog after revoking my own grant', async () => {
    grantsApi.revoke.mockResolvedValue(undefined)
    grantsApi.access.mockResolvedValue(
      accessList({
        items: [
          entry({
            principal: {
              type: 'user',
              id: 'user-owner',
              display_name: 'Owner',
              member_count: null,
            },
            level: 'owner',
            source: 'owner',
          }),
          entry({
            grant_id: 'grant-me',
            principal: { type: 'user', id: 'user-me', display_name: 'Me', member_count: null },
            level: 'manager',
            revocable: true,
          }),
        ],
        my_permissions: ['can_share', 'can_revoke'],
      }),
    )
    const { onOpenChange } = renderDialog()
    await userEvent.click(await screen.findByRole('button', { name: 'Me 공유 회수' }))
    await userEvent.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', { name: '회수' }),
    )
    await waitFor(() => expect(grantsApi.revoke).toHaveBeenCalledWith('grant-me'))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })

  it('closes after a transfer without keeping manager access', async () => {
    grantsApi.transfer.mockResolvedValue({})
    const { onOpenChange } = renderDialog()
    await userEvent.click(await screen.findByRole('button', { name: '소유권 이전' }))
    const combobox = screen.getByRole('combobox', { name: '새 소유자' })
    await waitFor(() => expect(combobox).toBeEnabled())
    await userEvent.type(combobox, 'Lee')
    await userEvent.click(await screen.findByRole('option', { name: /Lee Park/ }))
    await userEvent.click(screen.getByRole('checkbox', { name: /관리 권한 남기기/ }))
    const buttons = screen.getAllByRole('button', { name: '소유권 이전' })
    await userEvent.click(buttons[buttons.length - 1])
    await userEvent.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', { name: '소유권 이전' }),
    )
    await waitFor(() =>
      expect(grantsApi.transfer).toHaveBeenCalledWith('agent', 'agent-1', {
        new_owner_id: 'user-lee',
        reason: null,
        keep_manager: false,
      }),
    )
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })

  it('lists the personal accounts behind personal_account_shared', async () => {
    grantsApi.create.mockRejectedValue(
      new ApiError(409, 'personal_account_shared', 'personal', {
        personal_accounts: [
          {
            resource_type: 'tool',
            resource_id: 'tool-1',
            credential_id: 'cred-1',
            definition_key: 'google_workspace_oauth2',
          },
        ],
      }),
    )
    renderDialog()
    await pickRecipient('Lee Park')
    await userEvent.click(screen.getByRole('button', { name: '공유' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('개인 계정이 연결되어 있어 공유할 수 없어요')
    expect(alert).toHaveTextContent('도구 · google_workspace_oauth2')
    expect(toast.error).toHaveBeenCalledWith(
      '개인 계정 자격 증명이 연결된 자원은 공유할 수 없어요.',
    )
  })

  it('refreshes the resource list root after sharing', async () => {
    const spy = vi.spyOn(QueryClient.prototype, 'invalidateQueries')
    grantsApi.create.mockResolvedValue({})
    renderDialog({ resourceType: 'mcp_server' })
    await pickRecipient('Lee Park')
    await userEvent.click(screen.getByRole('button', { name: '공유' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ['mcp-servers'] }))
    expect(spy).toHaveBeenCalledWith({ queryKey: ['grants', 'mcp_server', 'agent-1'] })
    spy.mockRestore()
  })

  it('uses the local calendar day as the earliest expiry', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 9, 18, 0, 30))
    try {
      renderDialog()
      await userEvent.click(await screen.findByRole('radio', { name: /^실행/ }))
      expect(screen.getByLabelText('만료일 (선택)')).toHaveAttribute('min', '2026-10-18')
    } finally {
      vi.useRealTimers()
    }
  })
})
