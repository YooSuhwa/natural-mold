import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { ResourceShareDialog } from '@/components/sharing/resource-share-dialog'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from '../organizations/org-admin-mocks'
import { accessList, grantsApiMock, trapConsole } from './sharing-mocks'

vi.mock('@/lib/api/grants', async () => (await import('./sharing-mocks')).grantsApiMock)
vi.mock(
  '@/lib/api/organizations',
  async () => (await import('../organizations/org-admin-mocks')).orgApiMock,
)
vi.mock('@/lib/auth/session', () => ({ useSession: () => ({ data: { id: 'user-me' } }) }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { grantsApi } = grantsApiMock
const { meOrganizationsApi, orgAdminApi } = orgApiMock

const DELEGATIONS = [
  {
    resource_type: 'tool',
    resource_id: 'tool-1',
    name: 'Slack post',
    credential_id: 'cred-1',
    credential_name: 'Team Slack',
    owner_name: 'Me',
  },
  {
    resource_type: 'agent',
    resource_id: 'agent-1',
    name: 'Helper',
    credential_id: 'cred-2',
    credential_name: 'OpenAI',
    owner_name: null,
  },
]

function renderDialog() {
  render(
    <ResourceShareDialog
      open
      onOpenChange={vi.fn()}
      resourceType="agent"
      resourceId="agent-1"
      resourceName="Helper"
    />,
  )
}

async function pickRecipient() {
  const combobox = await screen.findByRole('combobox', { name: '공유할 사람 또는 그룹' })
  await waitFor(() => expect(combobox).toBeEnabled())
  await userEvent.type(combobox, 'Lee')
  await screen.findByRole('option', { name: /Lee Park/ })
  await userEvent.keyboard('{ArrowDown}{ArrowUp}{Enter}')
}

describe('ResourceShareDialog delegation preview (E-06)', () => {
  let consoleTrap: ReturnType<typeof trapConsole>

  beforeEach(() => {
    vi.clearAllMocks()
    consoleTrap = trapConsole()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    orgAdminApi.searchPrincipals.mockResolvedValue([
      { type: 'user', id: 'user-lee', display_name: 'Lee Park', email: null, member_count: null },
    ])
  })

  afterEach(() => {
    consoleTrap.assertClean()
    consoleTrap.restore()
  })

  it('lists the lent accounts before sharing and confirms before the request', async () => {
    grantsApi.access.mockResolvedValue(accessList({ delegations: DELEGATIONS }))
    grantsApi.create.mockResolvedValue({})
    renderDialog()

    const preview = await screen.findByRole('region', { name: '이 에이전트가 쓰는 계정 자원' })
    expect(within(preview).getByText('Slack post')).toBeInTheDocument()
    expect(within(preview).getByText('Team Slack · Me의 계정')).toBeInTheDocument()
    expect(within(preview).getByText('OpenAI · 회사 계정')).toBeInTheDocument()

    await pickRecipient()
    await userEvent.click(screen.getByRole('button', { name: '공유' }))
    const confirm = await screen.findByRole('group', { name: '위임된 계정 자원을 확인하세요' })
    expect(grantsApi.create).not.toHaveBeenCalled() // the list is confirmed first
    expect(within(confirm).getByText('Slack post')).toBeInTheDocument()

    await userEvent.click(within(confirm).getByRole('button', { name: '확인하고 공유' }))
    await waitFor(() => expect(grantsApi.create).toHaveBeenCalledTimes(1))
    expect(grantsApi.create.mock.calls[0][2]).toMatchObject({ confirm_delegation: true })
  })

  it('cancelling the confirmation sends nothing', async () => {
    grantsApi.access.mockResolvedValue(accessList({ delegations: DELEGATIONS }))
    renderDialog()
    await pickRecipient()
    await userEvent.click(screen.getByRole('button', { name: '공유' }))
    const confirm = await screen.findByRole('group', { name: '위임된 계정 자원을 확인하세요' })
    await userEvent.click(within(confirm).getByRole('button', { name: '취소' }))
    expect(grantsApi.create).not.toHaveBeenCalled()
    expect(screen.getByRole('region', { name: '이 에이전트가 쓰는 계정 자원' })).toBeInTheDocument()
  })

  it('shows no preview and shares directly when the agent lends no account', async () => {
    grantsApi.access.mockResolvedValue(accessList({ delegations: [] }))
    grantsApi.create.mockResolvedValue({})
    renderDialog()
    await pickRecipient()
    expect(screen.queryByRole('region', { name: '이 에이전트가 쓰는 계정 자원' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '공유' }))
    await waitFor(() => expect(grantsApi.create).toHaveBeenCalledTimes(1))
    expect(grantsApi.create.mock.calls[0][2]).toMatchObject({ confirm_delegation: false })
  })
})
