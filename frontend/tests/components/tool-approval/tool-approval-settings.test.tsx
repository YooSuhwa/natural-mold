import { ConversationGrantButton } from '@/features/tool-approval/components/conversation-tool-grants'
import { ToolApprovalSettings } from '@/features/tool-approval/components/tool-approval-settings'
import type { RuntimePolicyV1 } from '@/lib/types/runtime-policy'
import { render, screen, userEvent } from '../../test-utils'

const state = vi.hoisted(() => ({
  tools: [] as unknown[],
  grants: [] as unknown[],
  grant: vi.fn(),
}))

vi.mock('next/navigation', () => ({ useParams: () => ({ agentId: 'agent-1' }) }))
vi.mock('@/components/chat/conversation-context', () => ({ useChatConversationId: () => 'conv-1' }))
vi.mock('@/lib/hooks/use-tool-approval', () => ({
  useToolApproval: () => ({ data: { tools: state.tools } }),
  useToolGrants: () => ({ data: state.grants }),
  useToolGrantMutations: () => ({
    grant: { isPending: false, mutate: state.grant },
    revoke: { isPending: false, mutate: vi.fn() },
  }),
}))

const cell = (path: string, kind: string, reason = '위험 등급 기본값') => ({
  path,
  kind,
  layer: 'risk_default',
  reason,
  delivery: 'card',
  rule: null,
})

const POLICY: RuntimePolicyV1 = {
  version: 1,
  filesystem: { mode: 'artifact_write' },
  todo: { enabled: true },
  summarization: { mode: 'auto' },
}

describe('ToolApprovalSettings', () => {
  beforeEach(() => {
    state.tools = [
      {
        name: 'send_mail',
        level: 'external_mutation',
        locked: false,
        grantable: true,
        cells: [cell('chat', 'ask'), cell('workflow', 'auto')],
      },
      {
        name: 'execute_in_skill',
        level: 'code_execution',
        locked: true,
        grantable: false,
        cells: [cell('chat', 'ask', '회사 정책: 이 위험 등급은 승인 필요')],
      },
    ]
  })

  it('writes the path mode and a per-tool setting into the runtime policy', async () => {
    const onChange = vi.fn()
    render(<ToolApprovalSettings agentId="agent-1" value={POLICY} onValueChange={onChange} />)
    await userEvent.selectOptions(screen.getByLabelText('예약'), 'auto')
    expect(onChange.mock.calls[0][0]).toEqual({
      ...POLICY,
      tool_approval: { paths: { trigger: 'auto' } },
    })
    await userEvent.selectOptions(screen.getByLabelText('send_mail 승인 설정'), 'never')
    expect(onChange.mock.calls[1][0].tool_approval.tools).toEqual({
      send_mail: { mode: 'never' },
    })
    // The effective table and the company lock.
    expect(screen.getByRole('list', { name: 'send_mail 실제 적용' })).toHaveTextContent(
      '채팅 · 묻기',
    )
    expect(screen.getByLabelText('execute_in_skill 승인 설정')).toBeDisabled()
    expect(screen.getAllByText('회사 정책: 이 위험 등급은 승인 필요').length).toBeGreaterThan(0)
  })

  it('starts a custom policy when the agent had the recommended one', async () => {
    const onChange = vi.fn()
    render(<ToolApprovalSettings agentId="agent-1" value={null} onValueChange={onChange} />)
    expect(screen.getByText('바꾸면 런타임 정책이 직접 설정으로 저장됩니다.')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('채팅'), 'auto')
    expect(onChange.mock.calls[0][0]).toEqual({
      ...POLICY,
      tool_approval: { paths: { chat: 'auto' } },
    })
  })
})

describe('ConversationGrantButton', () => {
  it('shows only for a grantable tool not granted yet', async () => {
    state.grant.mockReset()
    state.tools = [
      { name: 'send_mail', level: 'external_mutation', locked: false, grantable: true, cells: [] },
      { name: 'run_code', level: 'code_execution', locked: true, grantable: false, cells: [] },
    ]
    state.grants = []
    const { rerender } = render(<ConversationGrantButton tool="run_code" />)
    expect(screen.queryByRole('button')).toBeNull()
    rerender(<ConversationGrantButton tool="send_mail" />)
    await userEvent.click(screen.getByRole('button', { name: '이 대화 동안 이 도구는 묻지 않기' }))
    expect(state.grant.mock.calls[0][0]).toBe('send_mail')
    state.grants = [{ id: 'g1', tool_name: 'send_mail', created_at: '' }]
    rerender(<ConversationGrantButton tool="send_mail" />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
