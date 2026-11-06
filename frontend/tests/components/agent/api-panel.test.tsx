import { ApiPanel } from '@/app/agents/[agentId]/settings/_components/right-panel/api-panel'
import type { AgentDeployment, DeploymentApprovalPolicy } from '@/lib/types'
import { render, screen, userEvent } from '../../test-utils'

const hooks = vi.hoisted(() => ({
  deployments: vi.fn(),
  update: vi.fn(),
}))

vi.mock('@/lib/hooks/use-agent-api', () => ({
  useAgentDeploymentCandidates: () => ({ data: [], isLoading: false }),
  useAgentDeployments: () => hooks.deployments(),
  useAgentApiKeys: () => ({ data: [], isLoading: false }),
  useCreateAgentDeployment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateAgentDeployment: () => ({ mutateAsync: hooks.update, isPending: false }),
}))

function deployment(approval_policy: DeploymentApprovalPolicy): AgentDeployment {
  return {
    id: 'dep-1',
    agent_id: 'agent-1',
    agent_name: 'Mail agent',
    public_id: 'mail-agent',
    status: 'active',
    allow_streaming: true,
    allow_background: false,
    rate_limit_per_minute: null,
    daily_token_limit: null,
    approval_policy,
    runtime_policy: null,
    created_at: '2026-11-07T00:00:00Z',
    updated_at: '2026-11-07T00:00:00Z',
  }
}

describe('ApiPanel approval policy (17.3)', () => {
  beforeEach(() => {
    hooks.update.mockReset().mockResolvedValue(undefined)
  })

  it('shows the current policy and switches a deployment to the inbox', async () => {
    hooks.deployments.mockReturnValue({ data: [deployment('blocked')], isLoading: false })
    const user = userEvent.setup()
    render(<ApiPanel agentId="agent-1" agentName="Mail agent" />)

    const blocked = screen.getByRole('button', { name: '실행하지 않고 넘어가기' })
    expect(blocked).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText(/BLOCKED 결과를 돌려줘요/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '승인함에서 기다리기' }))

    expect(hooks.update).toHaveBeenCalledWith({
      id: 'dep-1',
      data: { approval_policy: 'inbox' },
    })
  })

  it('does not resend the policy that is already set', async () => {
    hooks.deployments.mockReturnValue({ data: [deployment('inbox')], isLoading: false })
    const user = userEvent.setup()
    render(<ApiPanel agentId="agent-1" agentName="Mail agent" />)

    expect(screen.getByText(/GET \/v1\/runs\/:id/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '승인함에서 기다리기' }))

    expect(hooks.update).not.toHaveBeenCalled()
  })
})
