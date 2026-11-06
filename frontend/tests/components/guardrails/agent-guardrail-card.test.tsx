import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { AgentGuardrailCard } from '@/features/guardrails/components/agent-guardrail-card'
import type { AgentGuardrails, GuardrailRule } from '@/lib/types/guardrails'

const agentGuardrailsApi = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }))
vi.mock('@/lib/api/guardrails', () => ({ agentGuardrailsApi, guardrailsApi: {} }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const rule = (id: string, action: GuardrailRule['action']): GuardrailRule => ({
  id,
  packs: ['common'],
  stages: ['llm_send'],
  action,
  entities: null,
  restore_for_tools: false,
  pseudonym_style: 'tag',
})
const KR_DEFAULT = {
  ref: 'builtin:kr-default',
  name: 'kr-default',
  kind: 'builtin' as const,
  version: 3,
  rules: [rule('secrets', 'block'), rule('contacts', 'mask'), rule('people', 'pseudonymize')],
}
const KR_OBSERVE = {
  ref: 'builtin:kr-observe',
  name: 'kr-observe',
  kind: 'builtin' as const,
  version: 2,
  rules: [rule('observe', 'warn')],
}
const view = (overrides: Partial<AgentGuardrails> = {}): AgentGuardrails => ({
  mode: 'inherit',
  template_ref: null,
  rule_overrides: {},
  policy_mode: 'default',
  policy_default_template: KR_DEFAULT.ref,
  locked_rules: ['secrets'],
  effective: KR_DEFAULT,
  templates: [KR_DEFAULT, KR_OBSERVE],
  ...overrides,
})

describe('AgentGuardrailCard (에이전트 가드레일)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('turns off an open rule and changes another one’s action, never a locked rule', async () => {
    agentGuardrailsApi.get.mockResolvedValue(view())
    agentGuardrailsApi.put.mockResolvedValue(view())
    render(<AgentGuardrailCard agentId="agent-1" />)

    const card = await screen.findByTestId('agent-guardrail-card')
    expect(within(card).getByRole('switch', { name: '비밀값(API 키·토큰)' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(within(card).getByText('회사 잠금')).toBeInTheDocument()
    await userEvent.click(within(card).getByRole('switch', { name: '이름·주소' }))
    await userEvent.click(
      within(card).getByRole('combobox', { name: '연락처(전화·이메일) 처리 방식' }),
    )
    await userEvent.click(await screen.findByRole('option', { name: '치환' }))
    await userEvent.click(within(card).getByRole('button', { name: '가드레일 저장' }))

    expect(agentGuardrailsApi.put).toHaveBeenCalledWith('agent-1', {
      mode: 'inherit',
      template_ref: null,
      rule_overrides: {
        contacts: { enabled: true, action: 'redact' },
        people: { enabled: false, action: null },
      },
    })
  })

  it('chooses another template', async () => {
    agentGuardrailsApi.get.mockResolvedValue(view())
    agentGuardrailsApi.put.mockResolvedValue(view())
    render(<AgentGuardrailCard agentId="agent-1" />)

    await userEvent.click(await screen.findByRole('button', { name: '템플릿 선택' }))
    await userEvent.click(screen.getByRole('combobox', { name: '템플릿' }))
    await userEvent.click(await screen.findByRole('option', { name: /kr-observe/ }))
    await userEvent.click(screen.getByRole('button', { name: '가드레일 저장' }))

    expect(agentGuardrailsApi.put).toHaveBeenCalledWith('agent-1', {
      mode: 'template',
      template_ref: 'builtin:kr-observe',
      rule_overrides: {},
    })
  })

  it('says locked rules stay on when the agent switches off', async () => {
    agentGuardrailsApi.get.mockResolvedValue(view())
    render(<AgentGuardrailCard agentId="agent-1" />)

    await userEvent.click(await screen.findByRole('button', { name: '끔' }))

    expect(screen.getByText('꺼도 회사가 잠근 규칙 1개는 계속 적용됩니다.')).toBeInTheDocument()
  })

  it('keeps the company template when the policy is enforced', async () => {
    agentGuardrailsApi.get.mockResolvedValue(view({ policy_mode: 'enforced' }))
    render(<AgentGuardrailCard agentId="agent-1" />)

    expect(await screen.findByRole('button', { name: '끔' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '템플릿 선택' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '회사 따름' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('shows only a note when the company switched guardrails off', async () => {
    agentGuardrailsApi.get.mockResolvedValue(view({ policy_mode: 'off', effective: null }))
    render(<AgentGuardrailCard agentId="agent-1" />)

    expect(
      await screen.findByText('회사가 가드레일을 껐습니다. 이 에이전트는 검사하지 않습니다.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '가드레일 저장' })).not.toBeInTheDocument()
  })

  it('shows what the next run gets from the saved settings', async () => {
    agentGuardrailsApi.get.mockResolvedValue(view())
    render(<AgentGuardrailCard agentId="agent-1" />)

    await waitFor(() =>
      expect(screen.getByTestId('agent-guardrail-effective')).toHaveTextContent(
        '저장된 설정으로 다음 실행에 적용: kr-default v3 · 규칙 3개',
      ),
    )
  })
})
