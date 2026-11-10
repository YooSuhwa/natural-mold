import { toast } from 'sonner'

import { GuardrailToolPoliciesCard } from '@/features/guardrails/components/guardrail-tool-policies-card'
import { ApiError } from '@/lib/api/client'
import type { ToolPolicy, ToolPolicyTarget } from '@/lib/types/guardrails'
import { render, screen, userEvent, waitFor, within } from '../../test-utils'

const toolPoliciesApi = vi.hoisted(() => ({
  list: vi.fn(),
  targets: vi.fn(),
  save: vi.fn(),
  impact: vi.fn(),
}))

vi.mock('@/lib/api/guardrails', () => ({ toolPoliciesApi }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const TARGETS: ToolPolicyTarget[] = [
  { key: 'mcp_server:s1', name: 'Mail', kind: 'mcp_server', risk_level: null },
  {
    key: 'mcp:t1',
    name: 'send',
    kind: 'mcp_tool',
    server_key: 'mcp_server:s1',
    source: 'Mail',
    risk_level: 'external_mutation',
  },
  { key: 'builtin:web_search', name: 'web_search', kind: 'builtin', risk_level: 'read_only' },
]
const saved = (overrides: Partial<ToolPolicy> = {}): ToolPolicy => ({
  resource_key: 'mcp:t1',
  resource_name: 'send',
  enabled: null,
  default_mode: 'always',
  allowed_modes: ['always', 'when'],
  rule: null,
  updated_at: '2026-11-10T00:00:00Z',
  ...overrides,
})
const NO_IMPACT = { agents: [], total: 0, warnings: [] }

function editor() {
  return within(document.querySelector('[data-tool-policy-editor]') as HTMLElement)
}

// The targets load after the card: wait for the option before choosing it.
async function pick(key: string) {
  const add = await screen.findByLabelText('정책 추가')
  await waitFor(() => expect(add.querySelector(`option[value="${key}"]`)).not.toBeNull())
  await userEvent.selectOptions(add, key)
}

describe('GuardrailToolPoliciesCard (회사 도구별 정책)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    toolPoliciesApi.list.mockResolvedValue({ policies: [] })
    toolPoliciesApi.targets.mockResolvedValue(TARGETS)
    toolPoliciesApi.impact.mockResolvedValue(NO_IMPACT)
    toolPoliciesApi.save.mockResolvedValue({ policy: saved(), warnings: [] })
  })

  it('shows a member the saved policies and nothing to change them with', async () => {
    toolPoliciesApi.list.mockResolvedValue({
      policies: [saved(), saved({ resource_key: 'tool:x', resource_name: 'old', enabled: false })],
    })
    render(<GuardrailToolPoliciesCard tenantId="tenant-1" isAdmin={false} />)

    expect(await screen.findByText('send')).toBeInTheDocument()
    expect(screen.getByText('허용: 항상 묻기, 조건부')).toBeInTheDocument()
    expect(screen.getByText('기본: 항상 묻기')).toBeInTheDocument()
    expect(screen.getByText('사용 안 함')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /편집/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('정책 추가')).not.toBeInTheDocument()
    expect(toolPoliciesApi.targets).not.toHaveBeenCalled()
  })

  it('needs a default once the modes are limited, then previews and saves', async () => {
    render(<GuardrailToolPoliciesCard tenantId="tenant-1" isAdmin />)

    await pick('mcp:t1')
    expect(editor().getByText('Mail · send')).toBeInTheDocument()
    expect(editor().getByText('외부 변경')).toBeInTheDocument()
    // Nothing chosen yet: there is nothing to save.
    expect(editor().getByRole('button', { name: '저장' })).toBeDisabled()

    await userEvent.click(editor().getByRole('checkbox', { name: '묻지 않기' }))
    expect(editor().getByText('단계를 제한하면 기본 단계를 골라야 합니다.')).toBeInTheDocument()
    expect(editor().getByRole('button', { name: '저장' })).toBeDisabled()
    expect(
      within(editor().getByLabelText('기본 단계')).queryByRole('option', { name: '묻지 않기' }),
    ).not.toBeInTheDocument()

    await userEvent.selectOptions(editor().getByLabelText('기본 단계'), 'always')
    await userEvent.click(editor().getByRole('button', { name: '저장' }))

    const data = {
      enabled: null,
      default_mode: 'always',
      allowed_modes: ['always', 'when'],
      rule: null,
    }
    await waitFor(() =>
      expect(toolPoliciesApi.save).toHaveBeenCalledWith('tenant-1', 'mcp:t1', data),
    )
    expect(toolPoliciesApi.impact).toHaveBeenCalledWith('tenant-1', 'mcp:t1', data)
    expect(toast.success).toHaveBeenCalledWith('도구별 정책을 저장했습니다.')
    expect(document.querySelector('[data-tool-policy-editor]')).toBeNull()
  })

  it('fixes the default when one mode is left', async () => {
    render(<GuardrailToolPoliciesCard tenantId="tenant-1" isAdmin />)

    await pick('mcp:t1')
    await userEvent.click(editor().getByRole('checkbox', { name: '묻지 않기' }))
    await userEvent.click(editor().getByRole('checkbox', { name: '조건부' }))
    // The last mode cannot be unchecked.
    await userEvent.click(editor().getByRole('checkbox', { name: '항상 묻기' }))

    expect(editor().getByLabelText('기본 단계')).toHaveValue('always')
    expect(editor().getByLabelText('기본 단계')).toBeDisabled()
    await userEvent.click(editor().getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(toolPoliciesApi.save).toHaveBeenCalledWith('tenant-1', 'mcp:t1', {
        enabled: null,
        default_mode: 'always',
        allowed_modes: ['always'],
        rule: null,
      }),
    )
  })

  it('saves only after the admin has seen what stops applying', async () => {
    toolPoliciesApi.impact.mockResolvedValue({
      agents: [
        {
          id: 'a1',
          name: '상담 봇',
          tool_key: 'mcp:t1',
          tool_name: 'send',
          mode: 'never',
          reason: 'disabled',
        },
      ],
      total: 1,
      warnings: [],
    })
    render(<GuardrailToolPoliciesCard tenantId="tenant-1" isAdmin />)

    await pick('mcp_server:s1')
    await userEvent.selectOptions(editor().getByLabelText('사용 여부'), 'off')
    await userEvent.click(editor().getByRole('button', { name: '저장' }))

    expect(
      await editor().findByText('저장하면 에이전트 설정 1건이 적용되지 않습니다.'),
    ).toBeInTheDocument()
    expect(
      editor().getByText('상담 봇 · send: 도구를 쓸 수 없게 됩니다(묻지 않기 설정).'),
    ).toBeInTheDocument()
    expect(toolPoliciesApi.save).not.toHaveBeenCalled()

    // A changed draft is asked about again.
    await userEvent.selectOptions(editor().getByLabelText('사용 여부'), 'on')
    expect(editor().queryByText(/적용되지 않습니다/)).not.toBeInTheDocument()
    await userEvent.selectOptions(editor().getByLabelText('사용 여부'), 'off')
    await userEvent.click(editor().getByRole('button', { name: '저장' }))
    await userEvent.click(await editor().findByRole('button', { name: '이대로 저장' }))

    await waitFor(() =>
      expect(toolPoliciesApi.save).toHaveBeenCalledWith('tenant-1', 'mcp_server:s1', {
        enabled: false,
        default_mode: null,
        allowed_modes: null,
        rule: null,
      }),
    )
  })

  it('warns before a risky tool is set to run without asking', async () => {
    toolPoliciesApi.impact.mockResolvedValue({
      agents: [],
      total: 0,
      warnings: [{ code: 'risky_without_asking', risk_level: 'external_mutation' }],
    })
    render(<GuardrailToolPoliciesCard tenantId="tenant-1" isAdmin />)

    await pick('mcp:t1')
    await userEvent.selectOptions(editor().getByLabelText('기본 단계'), 'never')
    await userEvent.click(editor().getByRole('button', { name: '저장' }))

    expect(
      await editor().findByText(
        '이 도구는 외부 변경 등급인데 묻지 않고 실행됩니다. 감사 기록에 남습니다.',
      ),
    ).toBeInTheDocument()
    expect(toolPoliciesApi.save).not.toHaveBeenCalled()
  })

  it('edits a saved policy, removes it and offers only tools without one', async () => {
    toolPoliciesApi.list.mockResolvedValue({ policies: [saved()] })
    toolPoliciesApi.save.mockResolvedValue({ policy: null, warnings: [] })
    render(<GuardrailToolPoliciesCard tenantId="tenant-1" isAdmin />)

    const add = await screen.findByLabelText('정책 추가')
    await waitFor(() =>
      expect(within(add).getByRole('option', { name: 'web_search' })).toBeInTheDocument(),
    )
    expect(within(add).queryByRole('option', { name: 'Mail · send' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'send 정책 편집' }))
    expect(editor().getByRole('checkbox', { name: '묻지 않기' })).not.toBeChecked()
    expect(editor().getByLabelText('기본 단계')).toHaveValue('always')
    // Unchanged: nothing to save.
    expect(editor().getByRole('button', { name: '저장' })).toBeDisabled()
    await userEvent.click(editor().getByRole('button', { name: '정책 삭제' }))

    await waitFor(() =>
      expect(toolPoliciesApi.save).toHaveBeenCalledWith('tenant-1', 'mcp:t1', {
        enabled: null,
        default_mode: null,
        allowed_modes: null,
        rule: null,
      }),
    )
    expect(toast.success).toHaveBeenCalledWith('도구별 정책을 삭제했습니다.')
  })

  it('keeps the editor open and says why when the save is refused', async () => {
    toolPoliciesApi.save.mockRejectedValue(new ApiError(422, 'TOOL_POLICY_INVALID', 'x'))
    render(<GuardrailToolPoliciesCard tenantId="tenant-1" isAdmin />)

    await pick('mcp:t1')
    await userEvent.selectOptions(editor().getByLabelText('사용 여부'), 'on')
    await userEvent.click(editor().getByRole('button', { name: '저장' }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        '도구별 정책을 저장할 수 없습니다. 값을 확인해 주세요.',
      ),
    )
    expect(document.querySelector('[data-tool-policy-editor]')).not.toBeNull()
  })
})
