import AgentApiSettingsPage from '@/app/settings/agent-api/page'
import { ApiError } from '@/lib/api/errors'
import type { AgentApiKey, AgentDeploymentCandidate } from '@/lib/types'
import { render, screen } from '../../test-utils'

const hooks = vi.hoisted(() => ({
  candidates: vi.fn(),
  deployments: vi.fn(),
  keys: vi.fn(),
}))

vi.mock('@/lib/hooks/use-agent-api', () => ({
  useAgentDeploymentCandidates: () => hooks.candidates(),
  useAgentDeployments: () => hooks.deployments(),
  useAgentApiKeys: () => hooks.keys(),
  useCreateAgentDeployment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateAgentApiKey: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRevokeAgentApiKey: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-readiness', () => ({
  useAgentReadiness: () => ({ data: undefined, isError: false, refetch: vi.fn() }),
}))

function candidate(name: string): AgentDeploymentCandidate {
  return {
    agent_id: `agent-${name}`,
    agent_name: name,
    runtime_name: null,
    existing_deployment_id: null,
    existing_public_id: null,
    eligible: true,
    ineligible_reason: null,
    ineligible_reason_code: null,
    runtime_policy: null,
  }
}

function key(id: string, revoked: boolean): AgentApiKey {
  return {
    id,
    name: `키 ${id}`,
    description: null,
    key_id: id,
    prefix: 'mk_',
    last_four: '0000',
    scopes: ['invoke'],
    allow_all_deployments: true,
    deployments: [],
    revoked_at: revoked ? '2026-10-01T00:00:00Z' : null,
    expires_at: null,
    last_used_at: null,
    usage_count: 0,
    created_at: '2026-10-01T00:00:00Z',
  }
}

const ok = <T,>(data: T) => ({
  data,
  isLoading: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
})

describe('/settings/agent-api permissions (plan 13.9 row 4)', () => {
  beforeEach(() => {
    hooks.deployments.mockReturnValue(ok([]))
    hooks.keys.mockReturnValue(ok([]))
  })

  it('manager (can_deploy): the server candidate is offered for deployment', () => {
    hooks.candidates.mockReturnValue(ok([candidate('배포 가능')]))
    render(<AgentApiSettingsPage />)
    expect(screen.getByText('배포 가능')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /배포/ })).toBeEnabled()
  })

  it.each(['editor', 'viewer', 'runner'])(
    '%s (no can_deploy): nothing the server left out appears',
    () => {
      hooks.candidates.mockReturnValue(ok([]))
      render(<AgentApiSettingsPage />)
      expect(screen.queryByRole('button', { name: /배포/ })).not.toBeInTheDocument()
      expect(
        screen.getByText('API 엔드포인트로 배포할 에이전트를 먼저 만들어 주세요.'),
      ).toBeInTheDocument()
    },
  )

  it('key owner: own active keys can be revoked, revoked ones cannot', () => {
    hooks.candidates.mockReturnValue(ok([]))
    hooks.keys.mockReturnValue(ok([key('a', false), key('b', true)]))
    render(<AgentApiSettingsPage />)
    const [active, revoked] = screen.getAllByRole('button', { name: 'API 키 폐기' })
    expect(active).toBeEnabled()
    expect(revoked).toBeDisabled()
  })

  it('an authorization outage shows the shared error state', () => {
    hooks.candidates.mockReturnValue({
      ...ok(undefined),
      isError: true,
      error: new ApiError(503, 'authz_unavailable', 'x'),
    })
    render(<AgentApiSettingsPage />)
    expect(screen.getByText('권한을 확인하지 못했어요')).toBeInTheDocument()
  })
})
