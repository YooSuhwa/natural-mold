import { ConversationGrantButton } from '@/features/tool-approval/components/conversation-tool-grants'
import { ToolApprovalSettings } from '@/features/tool-approval/components/tool-approval-settings'
import type { RuntimePolicyV1 } from '@/lib/types/runtime-policy'
import { render, screen, userEvent, within } from '../../test-utils'

const state = vi.hoisted(() => ({
  tools: [] as unknown[],
  parts: [] as unknown[],
  grants: [] as unknown[],
  grant: vi.fn(),
}))

vi.mock('next/navigation', () => ({ useParams: () => ({ agentId: 'agent-1' }) }))
vi.mock('@/components/chat/conversation-context', () => ({ useChatConversationId: () => 'conv-1' }))
vi.mock('@/lib/hooks/use-tool-approval', () => ({
  useToolApproval: () => ({ data: { tools: state.tools, parts: state.parts } }),
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
    state.parts = []
    state.tools = [
      {
        key: 'tool:t1',
        name: 'send_mail',
        level: 'external_mutation',
        locked: false,
        grantable: true,
        cells: [cell('chat', 'ask'), cell('workflow', 'auto')],
      },
      {
        key: 'builtin:execute_in_skill',
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
      'tool:t1': { mode: 'never' },
    })
    // The effective table and the company lock.
    expect(screen.getByRole('list', { name: 'send_mail 실제 적용' })).toHaveTextContent(
      '채팅 · 묻기',
    )
    expect(screen.getByLabelText('execute_in_skill 승인 설정')).toBeDisabled()
    expect(screen.getAllByText('회사 정책: 이 위험 등급은 승인 필요').length).toBeGreaterThan(0)
  })

  it('sets a skill and a child agent by their keys, never by a name', async () => {
    state.parts = [
      {
        key: 'skill:k1',
        // The same name as a tool: the setting must still land on the skill's key.
        name: 'send_mail',
        kind: 'skill',
        level: 'code_execution',
        locked: false,
        cells: [cell('chat', 'ask')],
      },
      {
        key: 'agent:a1',
        name: 'Researcher',
        kind: 'agent',
        level: null,
        locked: false,
        cells: [cell('chat', 'auto')],
      },
      {
        key: 'skill:k2',
        name: 'locked_skill',
        kind: 'skill',
        level: 'code_execution',
        locked: true,
        allowed_modes: ['always'],
        cells: [cell('chat', 'ask', '회사 정책: 이 도구는 고정')],
      },
    ]
    const onChange = vi.fn()
    const value = {
      ...POLICY,
      tool_approval: { tools: { 'skill:k1': { mode: 'always' as const } } },
    }
    render(<ToolApprovalSettings agentId="agent-1" value={value} onValueChange={onChange} />)

    const parts = within(screen.getByRole('list', { name: '스킬과 하위 에이전트' }))
    expect(parts.getByText('하위 에이전트')).toBeInTheDocument()
    expect(parts.getByLabelText('send_mail 승인 설정')).toHaveValue('always')
    // A saved skill key is a known setting, not one that "needs checking".
    expect(screen.queryByRole('list', { name: '확인이 필요한 설정' })).not.toBeInTheDocument()

    await userEvent.selectOptions(parts.getByLabelText('Researcher 승인 설정'), 'always')
    expect(onChange.mock.calls[0][0].tool_approval.tools).toEqual({
      'skill:k1': { mode: 'always' },
      'agent:a1': { mode: 'always' },
    })
    await userEvent.selectOptions(parts.getByLabelText('send_mail 승인 설정'), 'default')
    expect(onChange.mock.calls[1][0].tool_approval.tools ?? {}).toEqual({})
    expect(parts.getByLabelText('locked_skill 승인 설정')).toBeDisabled()
  })

  it('shows no skill section for an agent without skills or child agents', () => {
    render(<ToolApprovalSettings agentId="agent-1" value={POLICY} onValueChange={vi.fn()} />)
    expect(screen.queryByText('스킬과 하위 에이전트')).not.toBeInTheDocument()
  })

  it('offers only the modes the company allows for a tool', () => {
    state.tools = [
      {
        key: 'tool:t1',
        name: 'send_mail',
        level: 'external_mutation',
        locked: false,
        grantable: true,
        allowed_modes: ['always', 'when'],
        cells: [cell('chat', 'ask')],
      },
      {
        key: 'tool:t2',
        name: 'read_mail',
        level: 'read_only',
        locked: false,
        grantable: true,
        allowed_modes: null,
        cells: [cell('chat', 'auto')],
      },
    ]
    const policy = {
      ...POLICY,
      tool_approval: { tools: { 'tool:t2': { mode: 'never' as const } } },
    }
    render(<ToolApprovalSettings agentId="agent-1" value={policy} onValueChange={vi.fn()} />)

    const limited = within(screen.getByLabelText('send_mail 승인 설정'))
    expect(limited.getByRole('option', { name: '묻지 않기' })).toBeDisabled()
    expect(limited.getByRole('option', { name: '항상 묻기' })).toBeEnabled()
    expect(limited.getByRole('option', { name: '조건부' })).toBeEnabled()
    expect(limited.getByRole('option', { name: '기본(모드를 따름)' })).toBeEnabled()
    expect(screen.getByText('회사가 허용한 단계: 항상 묻기, 조건부')).toBeInTheDocument()
    const free = within(screen.getByLabelText('read_mail 승인 설정'))
    expect(free.getByRole('option', { name: '묻지 않기' })).toBeEnabled()
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

describe('ToolApprovalSettings keys', () => {
  const mcp = (key: string, source: string) => ({
    key,
    name: 'search',
    source,
    level: 'external_mutation',
    locked: false,
    grantable: false,
    cells: [],
  })

  it('reads a setting saved by name and moves it to the key on change', async () => {
    state.tools = [
      { key: 'tool:t1', name: 'send_mail', level: 'x', locked: false, grantable: true, cells: [] },
    ]
    const onChange = vi.fn()
    const value = {
      ...POLICY,
      tool_approval: { tools: { send_mail: { mode: 'always' as const } } },
    }
    render(<ToolApprovalSettings agentId="agent-1" value={value} onValueChange={onChange} />)
    expect(screen.getByLabelText('send_mail 승인 설정')).toHaveValue('always')
    expect(screen.queryByRole('list', { name: '확인이 필요한 설정' })).toBeNull()
    await userEvent.selectOptions(screen.getByLabelText('send_mail 승인 설정'), 'never')
    expect(onChange.mock.calls[0][0].tool_approval.tools).toEqual({ 'tool:t1': { mode: 'never' } })
  })

  it('sets two same-named tools apart and reports what names no single tool', async () => {
    state.tools = [mcp('mcp:a', 'Docs'), mcp('mcp:b', 'Wiki')]
    const onChange = vi.fn()
    const value = {
      ...POLICY,
      tool_approval: {
        tools: {
          'mcp:a': { mode: 'always' as const },
          search: { mode: 'never' as const },
          gone: { mode: 'always' as const },
        },
      },
    }
    render(<ToolApprovalSettings agentId="agent-1" value={value} onValueChange={onChange} />)
    expect(screen.getByLabelText('search (Docs) 승인 설정')).toHaveValue('always')
    // The name entry cannot say which of the two it means.
    expect(screen.getByLabelText('search (Wiki) 승인 설정')).toHaveValue('default')
    const list = screen.getByRole('list', { name: '확인이 필요한 설정' })
    expect(list).toHaveTextContent('search')
    expect(list).toHaveTextContent('gone')
    await userEvent.selectOptions(screen.getByLabelText('search (Wiki) 승인 설정'), 'always')
    expect(onChange.mock.calls[0][0].tool_approval.tools).toEqual({
      'mcp:a': { mode: 'always' },
      'mcp:b': { mode: 'always' },
      search: { mode: 'never' },
      gone: { mode: 'always' },
    })
    await userEvent.click(screen.getByRole('button', { name: 'gone 설정 지우기' }))
    expect(onChange.mock.calls[1][0].tool_approval.tools).toEqual({
      'mcp:a': { mode: 'always' },
      search: { mode: 'never' },
    })
  })
})

describe('ConversationGrantButton', () => {
  it('shows only for a grantable tool not granted yet', async () => {
    state.grant.mockReset()
    state.tools = [
      {
        key: 'tool:t1',
        name: 'send_mail',
        level: 'external_mutation',
        locked: false,
        grantable: true,
        cells: [],
      },
      {
        key: 'builtin:run_code',
        name: 'run_code',
        level: 'code_execution',
        locked: true,
        grantable: false,
        cells: [],
      },
    ]
    state.grants = []
    const { rerender } = render(<ConversationGrantButton tool="run_code" />)
    expect(screen.queryByRole('button')).toBeNull()
    rerender(<ConversationGrantButton tool="send_mail" />)
    await userEvent.click(screen.getByRole('button', { name: '이 대화 동안 이 도구는 묻지 않기' }))
    // The grant is made under the tool's key, and found there afterwards.
    expect(state.grant.mock.calls[0][0]).toBe('tool:t1')
    state.grants = [{ id: 'g1', tool_name: 'tool:t1', created_at: '' }]
    rerender(<ConversationGrantButton tool="send_mail" />)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('hides for a name two tools share', () => {
    const row = (key: string) => ({
      key,
      name: 'search',
      level: 'read_only',
      locked: false,
      grantable: true,
      cells: [],
    })
    state.grants = []
    state.tools = [row('mcp:a'), row('mcp:b')]
    render(<ConversationGrantButton tool="search" />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
