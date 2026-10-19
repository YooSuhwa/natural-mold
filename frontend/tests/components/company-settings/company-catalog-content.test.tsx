import { render, screen, userEvent, waitFor } from '../../test-utils'
import { CompanyCatalogContent } from '@/features/company-settings/components/company-catalog-content'
import { ApiError } from '@/lib/api/client'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from '../organizations/org-admin-mocks'
import { makeOverview, pending, tenantCatalogApiMock, VERSION } from './company-mocks'

vi.mock(
  '@/lib/api/organizations',
  async () => (await import('../organizations/org-admin-mocks')).orgApiMock,
)
vi.mock(
  '@/lib/api/tenant-catalog',
  async () => (await import('./company-mocks')).tenantCatalogApiMock,
)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const api = tenantCatalogApiMock.tenantCatalogApi
const SECRET_VALUE = 'sk-never-shown'

const model = (overrides = {}) => ({
  id: 'm-1',
  source: 'platform',
  provider: 'anthropic',
  model_name: 'claude-sonnet',
  display_name: 'Claude Sonnet',
  base_url: null,
  is_default: false,
  is_visible: true,
  default_credential_id: null,
  company_enabled: true,
  requires_company_key: false,
  allowed_org_ids: null,
  ...overrides,
})

describe('CompanyCatalogContent (S07)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    orgApiMock.meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    orgApiMock.tenantOrganizationsApi.list.mockResolvedValue({
      view: 'admin',
      items: [
        { id: 'org-1', name: '인사팀', slug: 'hr', status: 'active', is_default: true },
        { id: 'org-2', name: '연구소', slug: 'lab', status: 'active', is_default: false },
      ],
      next_cursor: null,
      has_more: false,
    })
    api.listModels.mockResolvedValue({
      items: [
        model(),
        model({
          id: 'm-2',
          source: 'company',
          display_name: '사내 Qwen',
          default_credential_id: 'c-1',
          allowed_org_ids: ['org-2'],
        }),
      ],
      allow_system_models: true,
    })
    api.listCredentials.mockResolvedValue([
      {
        id: 'c-1',
        definition_key: 'openai_compatible',
        name: '사내 vLLM 키',
        field_keys: ['api_key'],
        status: 'active',
        allowed_org_ids: ['org-2'],
        created_at: '2026-10-01T00:00:00Z',
        updated_at: '2026-10-01T00:00:00Z',
      },
    ])
    api.getCatalogPolicy.mockResolvedValue({
      items: [
        {
          type: 'tool',
          id: 't-1',
          name: 'Web Search',
          source: 'platform',
          platform_available: true,
          company_enabled: true,
        },
        {
          type: 'skill',
          id: 's-1',
          name: 'Hidden Skill',
          source: 'platform',
          platform_available: false,
          company_enabled: true,
        },
      ],
      allow_system_models: true,
      updated_at: VERSION,
    })
    api.getLlmSettings.mockResolvedValue({
      items: [
        {
          role: 'text_primary',
          inherits: false,
          credential_id: 'c-1',
          credential_name: '사내 vLLM 키',
          provider: 'openai',
          model_name: 'qwen-72b',
          configured: true,
        },
        {
          role: 'image',
          inherits: true,
          credential_id: null,
          credential_name: null,
          provider: null,
          model_name: null,
          configured: false,
        },
      ],
      allow_system_models: true,
    })
  })

  it('lets a company admin narrow models, edit the catalog and service LLM', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    api.updateModel.mockResolvedValue(model())
    api.putCatalogPolicy.mockResolvedValue({})
    api.putLlmSetting.mockResolvedValue({ items: [], allow_system_models: true })
    render(<CompanyCatalogContent />)

    expect(await screen.findByText('사내 Qwen')).toBeInTheDocument()
    expect(await screen.findByText('연구소')).toBeInTheDocument()
    expect(screen.getAllByText('사내 vLLM 키').length).toBeGreaterThan(0)
    // Platform models offer no delete; company models do.
    expect(screen.queryByRole('button', { name: 'Claude Sonnet 삭제' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '사내 Qwen 삭제' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Claude Sonnet 수정' }))
    await userEvent.click(await screen.findByRole('checkbox', { name: '모든 조직' }))
    await userEvent.click(screen.getByRole('checkbox', { name: '연구소' }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(api.updateModel).toHaveBeenCalledWith('tenant-1', 'm-1', { allowed_org_ids: ['org-2'] })

    await userEvent.click(screen.getByRole('tab', { name: '공용 자격증명' }))
    expect(await screen.findByText('api_key')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(SECRET_VALUE)

    await userEvent.click(screen.getByRole('tab', { name: '회사 카탈로그' }))
    expect(await screen.findByRole('switch', { name: 'Hidden Skill 사용' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    await userEvent.click(screen.getByRole('switch', { name: 'Web Search 사용' }))
    await userEvent.click(screen.getByRole('button', { name: '카탈로그 저장' }))
    expect(api.putCatalogPolicy).toHaveBeenCalledWith(
      'tenant-1',
      [{ type: 'tool', id: 't-1', enabled: false }],
      VERSION,
    )

    await userEvent.click(screen.getByRole('tab', { name: '서비스 LLM' }))
    expect(await screen.findByText('회사 값: 사내 vLLM 키 / qwen-72b')).toBeInTheDocument()
    await userEvent.click(
      screen.getByRole('button', { name: '기본 텍스트 모델 플랫폼 기본값 따르기' }),
    )
    expect(api.putLlmSetting).toHaveBeenCalledWith('tenant-1', {
      role: 'text_primary',
      credential_id: null,
      model_name: null,
    })
  })

  it.each(['member', 'auditor'] as const)('fails closed for a company %s', async (access) => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview(access))
    render(<CompanyCatalogContent />)
    expect(await screen.findByText('회사 관리자만 볼 수 있습니다')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: '모델' })).not.toBeInTheDocument()
    expect(api.listModels).not.toHaveBeenCalled()
  })

  it('renders no tabs while the company authority is loading', async () => {
    orgApiMock.tenantsApi.overview.mockReturnValue(pending())
    render(<CompanyCatalogContent />)
    expect(await screen.findByTestId('company-loading')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: '모델' })).not.toBeInTheDocument()
  })

  it('shows the shared error state when the server refuses the model list', async () => {
    orgApiMock.tenantsApi.overview.mockResolvedValue(makeOverview('admin'))
    api.listModels.mockRejectedValue(new ApiError(403, 'permission_required', 'no'))
    render(<CompanyCatalogContent />)
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveAttribute('data-error-kind', 'permission_required'),
    )
    expect(screen.queryByText('사내 Qwen')).not.toBeInTheDocument()
  })
})
