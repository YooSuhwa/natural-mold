import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { toast } from 'sonner'
import { CompanyGuardrailsContent } from '@/features/guardrails/components/company-guardrails-content'
import { ApiError } from '@/lib/api/client'
import { DEFAULT_TOOL_RISK } from '@/features/guardrails/lib/labels'
import type { GuardrailRule, GuardrailTemplate } from '@/lib/types/guardrails'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from '../organizations/org-admin-mocks'
import { makeOverview, VERSION } from '../company-settings/company-mocks'

const guardrailsApi = vi.hoisted(() => ({
  getPolicy: vi.fn(),
  putPolicy: vi.fn(),
  listTemplates: vi.fn(),
  createTemplate: vi.fn(),
  updateTemplate: vi.fn(),
  publishTemplate: vi.fn(),
  archiveTemplate: vi.fn(),
}))

vi.mock(
  '@/lib/api/organizations',
  async () => (await import('../organizations/org-admin-mocks')).orgApiMock,
)
vi.mock('@/lib/api/guardrails', () => ({ guardrailsApi }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const rule = (id: string, overrides: Partial<GuardrailRule> = {}): GuardrailRule => ({
  id,
  packs: ['common'],
  stages: ['llm_send'],
  action: 'block',
  entities: null,
  restore_for_tools: false,
  pseudonym_style: 'tag',
  ...overrides,
})
const DEFAULT_RULES = [
  rule('secrets'),
  rule('people', { action: 'pseudonymize', restore_for_tools: true }),
]
const policy = (overrides = {}) => ({
  mode: 'default',
  default_template: 'builtin:kr-default',
  locked_rules: [],
  tool_risk: DEFAULT_TOOL_RISK,
  saved: true,
  updated_at: VERSION,
  templates: [
    {
      ref: 'builtin:kr-default',
      name: 'kr-default',
      kind: 'builtin',
      version: 3,
      rules: DEFAULT_RULES,
    },
    {
      ref: 'builtin:kr-observe',
      name: 'kr-observe',
      kind: 'builtin',
      version: 2,
      rules: [rule('observe', { action: 'warn' })],
    },
  ],
  ...overrides,
})
const template = (overrides: Partial<GuardrailTemplate> = {}): GuardrailTemplate => ({
  id: 'tpl-1',
  ref: 'company:tpl-1',
  name: '고객 상담',
  description: '',
  status: 'draft',
  draft_rules: DEFAULT_RULES,
  published_version: null,
  updated_at: VERSION,
  ...overrides,
})

describe('CompanyGuardrailsContent (회사 가드레일)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    orgApiMock.meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    guardrailsApi.getPolicy.mockResolvedValue(policy())
    guardrailsApi.listTemplates.mockResolvedValue([template()])
  })

  it('lets a company admin enforce the default and lock a rule', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    guardrailsApi.putPolicy.mockResolvedValue(policy({ mode: 'enforced' }))
    render(<CompanyGuardrailsContent />)

    await userEvent.click(await screen.findByRole('button', { name: '강제' }))
    const locks = screen.getByRole('group', { name: '잠근 규칙' })
    await userEvent.click(within(locks).getByRole('checkbox', { name: '비밀값(API 키·토큰)' }))
    await userEvent.click(screen.getByRole('button', { name: '정책 저장' }))

    expect(guardrailsApi.putPolicy).toHaveBeenCalledWith(
      'tenant-1',
      { mode: 'enforced', default_template: 'builtin:kr-default', locked_rules: ['secrets'] },
      VERSION,
    )
  })

  it('saves the actions per tool risk level with the policy as it is', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    guardrailsApi.putPolicy.mockResolvedValue(policy())
    render(<CompanyGuardrailsContent />)

    const external = await screen.findByRole('group', { name: '외부 변경' })
    await userEvent.click(within(external).getByRole('button', { name: '차단' }))
    const readOnly = screen.getByRole('group', { name: '조회' })
    await userEvent.click(within(readOnly).getByRole('button', { name: '승인 필요' }))
    await userEvent.click(screen.getByRole('button', { name: '기본 조치 저장' }))

    expect(guardrailsApi.putPolicy).toHaveBeenCalledWith(
      'tenant-1',
      {
        mode: 'default',
        default_template: 'builtin:kr-default',
        locked_rules: [],
        tool_risk: { ...DEFAULT_TOOL_RISK, external_mutation: 'block', read_only: 'approval' },
      },
      VERSION,
    )
  })

  it('maps a guardrail error code to its message', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    guardrailsApi.putPolicy.mockRejectedValue(new ApiError(422, 'GUARDRAIL_TEMPLATE_UNUSABLE', 'x'))
    render(<CompanyGuardrailsContent />)

    await userEvent.click(await screen.findByRole('button', { name: '강제' }))
    await userEvent.click(screen.getByRole('button', { name: '정책 저장' }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        '고른 템플릿을 쓸 수 없습니다. 게시된 템플릿인지 확인해 주세요.',
      ),
    )
  })

  it('shows company members the policy read-only and no templates', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('member'))
    render(<CompanyGuardrailsContent />)

    expect(await screen.findByText(/회사 구성원은 정책을 볼 수만 있습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '강제' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '정책 저장' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '기본 조치 저장' })).not.toBeInTheDocument()
    expect(
      within(screen.getByRole('group', { name: '외부 변경' })).getByRole('button', {
        name: '차단',
      }),
    ).toBeDisabled()
    expect(screen.queryByText('회사 템플릿')).not.toBeInTheDocument()
    expect(guardrailsApi.listTemplates).not.toHaveBeenCalled()
  })

  it('edits a draft rule and publishes the template', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    guardrailsApi.updateTemplate.mockResolvedValue(template())
    guardrailsApi.publishTemplate.mockResolvedValue(
      template({ status: 'published', published_version: 1 }),
    )
    render(<CompanyGuardrailsContent />)

    await userEvent.click(await screen.findByRole('button', { name: '고객 상담 편집' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(
      within(within(dialog).getByRole('group', { name: '이름·주소 · 검사 단계' })).getByRole(
        'checkbox',
        { name: '답변' },
      ),
    )
    await userEvent.click(
      within(dialog).getByRole('button', { name: '비밀값(API 키·토큰) 규칙 삭제' }),
    )
    await userEvent.click(within(dialog).getByRole('button', { name: '초안 저장' }))

    expect(guardrailsApi.updateTemplate).toHaveBeenCalledWith(
      'tenant-1',
      'tpl-1',
      {
        name: '고객 상담',
        description: '',
        rules: [{ ...DEFAULT_RULES[1], stages: ['llm_send', 'output'] }],
      },
      VERSION,
    )
    await userEvent.click(screen.getByRole('button', { name: '고객 상담 게시' }))
    expect(guardrailsApi.publishTemplate).toHaveBeenCalledWith('tenant-1', 'tpl-1', VERSION)
  })

  it('creates a template from a built-in one', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    guardrailsApi.createTemplate.mockResolvedValue(template())
    render(<CompanyGuardrailsContent />)

    await userEvent.click(await screen.findByRole('button', { name: '새 템플릿' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/이름/), '고객 상담')
    await userEvent.click(within(dialog).getByRole('button', { name: '만들기' }))

    expect(guardrailsApi.createTemplate).toHaveBeenCalledWith('tenant-1', {
      name: '고객 상담',
      description: '',
      copy_from: 'builtin:kr-default',
    })
  })

  it('explains why the default template cannot be archived', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    guardrailsApi.archiveTemplate.mockRejectedValue(
      new ApiError(409, 'GUARDRAIL_TEMPLATE_IN_USE', 'x'),
    )
    render(<CompanyGuardrailsContent />)

    await userEvent.click(await screen.findByRole('button', { name: '고객 상담 보관' }))
    await userEvent.click(await screen.findByRole('button', { name: '보관하기' }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        '회사 기본 템플릿은 보관할 수 없습니다. 먼저 정책의 기본 템플릿을 바꾸세요.',
      ),
    )
  })
})
