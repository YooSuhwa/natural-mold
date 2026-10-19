import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { ApiError } from '@/lib/api/errors'
import { DelegationConfirmHost } from '@/components/sharing/delegation-confirm-host'
import {
  DelegationDeclinedError,
  updateAgentConfirmingDelegation,
} from '@/lib/sharing/delegation-confirm-request'
import { trapConsole } from './sharing-mocks'

const update = vi.fn()
vi.mock('@/lib/api/agents', () => ({
  agentsApi: { update: (...args: unknown[]) => update(...args) },
}))

const CONFIRM = new ApiError(409, 'confirm_required', 'confirm', {
  delegations: [
    {
      resource_type: 'tool',
      resource_id: 'tool-1',
      name: 'Slack post',
      credential_id: 'cred-1',
      credential_name: 'Team Slack',
      owner_name: 'Kim',
    },
  ],
})

describe('agent settings save on a shared agent (E-06)', () => {
  let consoleTrap: ReturnType<typeof trapConsole>

  beforeEach(() => {
    update.mockReset()
    consoleTrap = trapConsole()
  })

  afterEach(() => {
    consoleTrap.assertClean()
    consoleTrap.restore()
  })

  it('asks on confirm_required and resends with confirm_delegation', async () => {
    update.mockRejectedValueOnce(CONFIRM).mockResolvedValueOnce({ id: 'agent-1' })
    render(<DelegationConfirmHost />)
    const saving = updateAgentConfirmingDelegation('agent-1', { tool_ids: ['tool-1'] })

    const dialog = await screen.findByRole('dialog', {
      name: '공유된 에이전트에 계정 자원을 연결해요',
    })
    expect(within(dialog).getByText('Slack post')).toBeInTheDocument()
    expect(within(dialog).getByText('Team Slack · Kim의 계정')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '확인하고 저장' }))

    await expect(saving).resolves.toEqual({ id: 'agent-1' })
    expect(update).toHaveBeenCalledTimes(2)
    expect(update.mock.calls[1]).toEqual([
      'agent-1',
      { tool_ids: ['tool-1'], confirm_delegation: true },
    ])
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('cancelling refuses the save without resending', async () => {
    update.mockRejectedValueOnce(CONFIRM)
    render(<DelegationConfirmHost />)
    const outcome = updateAgentConfirmingDelegation('agent-1', { tool_ids: ['tool-1'] }).catch(
      (error: unknown) => error,
    )
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: '취소' }))
    expect(await outcome).toBeInstanceOf(DelegationDeclinedError)
    expect(update).toHaveBeenCalledTimes(1)
  })

  it('passes other errors through unchanged', async () => {
    const other = new ApiError(409, 'personal_account_shared', 'no')
    update.mockRejectedValueOnce(other)
    await expect(updateAgentConfirmingDelegation('agent-1', {})).rejects.toBe(other)
  })
})
