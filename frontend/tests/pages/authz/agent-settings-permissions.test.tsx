/**
 * H-05 / plan 13.9 row 2: agent settings per permission level. The route gate turns a
 * missing `can_view_config` into the permission notice; viewers get a read-only screen;
 * share/API/fix follow `can_share`/`can_deploy`/`can_edit`.
 */
import { render, screen } from '../../test-utils'
import AgentSettingsPage from '@/app/agents/[agentId]/settings/page'
import { AgentSettingsGate } from '@/components/agent/agent-settings-gate'
import { ApiError } from '@/lib/api/errors'
import { mockAgent } from '../../mocks/fixtures'

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('react', async () => {
  const actual = await vi.importActual('react')
  return {
    ...actual,
    use: (value: unknown) => {
      if (value && typeof value === 'object' && 'agentId' in value) return value
      return (actual as Record<string, unknown>).use(value)
    },
  }
})

const mockUseAgent = vi.fn()
vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => ({ data: [], isLoading: false }),
  useAgent: (...args: unknown[]) => mockUseAgent(...args),
  useUpdateAgent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteAgent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useGenerateAgentImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-models', () => ({ useModels: () => ({ data: [] }) }))
vi.mock('@/lib/hooks/use-tools', () => ({ useTools: () => ({ data: [] }) }))
vi.mock('@/lib/hooks/use-skills', () => ({ useSkills: () => ({ data: [] }) }))
vi.mock('@/lib/hooks/use-middlewares', () => ({ useMiddlewares: () => ({ data: [] }) }))
vi.mock('@/lib/hooks/use-mcp-servers', () => ({
  useAllMcpTools: () => ({ data: [], isLoading: false }),
}))
vi.mock('@/lib/hooks/use-memory', () => ({
  useAgentMemorySettings: () => ({ data: undefined, isLoading: false }),
  useUpdateAgentMemorySettings: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-triggers', () => ({
  useTriggers: () => ({ data: [] }),
  useCreateTrigger: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateTrigger: () => ({ mutate: vi.fn() }),
  useDeleteTrigger: () => ({ mutate: vi.fn(), isPending: false }),
}))
vi.mock('@/components/model/model-select', () => ({ ModelSelect: () => <select /> }))
vi.mock('@/components/agent/assistant-panel', () => ({
  AssistantPanel: () => <div data-testid="assistant-panel" />,
}))
vi.mock('@/components/ui/slider', () => ({ Slider: () => <div /> }))
vi.mock('@/components/sharing/resource-share-button', () => ({
  ResourceShareButton: () => <button type="button">에이전트 공유</button>,
}))

const ALL = [
  'can_run',
  'can_view_config',
  'can_clone',
  'can_edit',
  'can_share',
  'can_deploy',
  'can_delete',
]
const EDITOR = ['can_run', 'can_view_config', 'can_clone', 'can_edit']
const VIEWER = ['can_run', 'can_view_config', 'can_clone']

const agent = {
  ...mockAgent,
  runtime_name: 'test_agent',
  identity_mode: 'per_user',
  mcp_tools: [],
  skills: [],
  sub_agents: [],
  is_favorite: false,
  model_params: null,
  middleware_configs: [],
}

function renderAs(data: unknown, error: unknown = null) {
  mockUseAgent.mockReturnValue({
    data,
    isLoading: false,
    isError: !!error,
    error,
    refetch: vi.fn(),
  })
  return render(
    <AgentSettingsGate agentId="agent-1">
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />
    </AgentSettingsGate>,
  )
}

function expectEditor({ share, deploy }: { share: boolean; deploy: boolean }) {
  expect(screen.getByRole('button', { name: '저장' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: /Fix 에이전트/ })).toBeInTheDocument()
  expect(screen.getByTestId('agent-settings-form')).not.toBeDisabled()
  expect(screen.getByDisplayValue('Test Agent')).not.toHaveAttribute('readonly')
  expect(!!screen.queryByRole('button', { name: '에이전트 공유' })).toBe(share)
  expect(!!screen.queryByRole('tab', { name: /API/ })).toBe(deploy)
}

describe('agent settings per permission level (plan 13.9)', () => {
  it('owner, legacy body without my_permissions: everything', () => {
    renderAs(agent)
    expectEditor({ share: true, deploy: true })
    expect(screen.getByLabelText('에이전트 삭제')).toBeInTheDocument()
  })

  it('owner with every permission: everything', () => {
    renderAs({ ...agent, my_permissions: ALL })
    expectEditor({ share: true, deploy: true })
    expect(screen.getByLabelText('에이전트 삭제')).toBeInTheDocument()
  })

  it('editor: edits, but no share, API panel or delete', () => {
    renderAs({ ...agent, my_permissions: EDITOR })
    expectEditor({ share: false, deploy: false })
    expect(screen.queryByLabelText('에이전트 삭제')).not.toBeInTheDocument()
  })

  it('viewer: read-only screen', () => {
    renderAs({ ...agent, my_permissions: VIEWER })
    expect(
      screen.getByText('보기 전용이에요. 수정하려면 편집 권한이 필요해요.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '저장' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('에이전트 삭제')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '에이전트 공유' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /Fix 에이전트/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /API/ })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /테스트/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('agent-settings-form')).toBeDisabled()
    expect(screen.getByDisplayValue('Test Agent')).toHaveAttribute('readonly')
  })

  it('runner: the whole route is the permission notice with a request button', () => {
    renderAs({ id: 'agent-1', view: 'runner', name: 'Test Agent', my_permissions: ['can_run'] })
    expect(screen.getByText('권한이 필요해요')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '권한 요청' })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /폼/ })).not.toBeInTheDocument()
  })

  it('none: a hidden agent is not found, a visible one offers a request', () => {
    const { unmount } = renderAs(undefined, new ApiError(404, 'AGENT_NOT_FOUND', 'x'))
    expect(screen.getByText('찾을 수 없어요')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /폼/ })).not.toBeInTheDocument()
    unmount()
    renderAs(undefined, new ApiError(403, 'permission_required', 'x', { request_access: true }))
    expect(screen.getByRole('button', { name: '권한 요청' })).toBeInTheDocument()
  })
})
