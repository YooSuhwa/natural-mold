import { OrgPolicySection } from '@/features/organizations/components/org-policy-section'
import { imagePolicyDestination } from '@/features/organizations/lib/image-policy-model'
import { modelsApi } from '@/lib/api/models'
import { mockModel } from '../../mocks/fixtures'
import { render, screen, userEvent, waitFor } from '../../test-utils'
import { governanceApiMock, makeOrgView, orgApiMock } from './org-admin-mocks'

vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock(
  '@/lib/api/org-governance',
  async () => (await import('./org-admin-mocks')).governanceApiMock,
)
vi.mock('@/lib/api/models', () => ({ modelsApi: { list: vi.fn() } }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const visionModel = {
  ...mockModel,
  id: '12121212-1212-1212-1212-121212121212',
  display_name: 'Vision One',
  supports_vision: true,
  default_credential_id: 'credential-1',
  context_window: 32_000,
  cost_per_input_token: 0.000001,
  cost_per_output_token: 0.000005,
}
const persistedPolicy = {
  model_id: visionModel.id,
  provider: 'openai' as const,
  base_url: 'https://api.openai.com/v1',
  max_input_bytes: 4194304,
  max_output_tokens: 256,
  timeout_seconds: 15,
  max_cost_usd: '0.50',
}
const getCatalog = governanceApiMock.orgGovernanceApi.getCatalog
const catalog = [visionModel.id, 'text-model', 'unbound', 'hidden', 'unpriced', 'org-disabled'].map(
  (id) => ({
    type: 'model',
    id,
    name: id,
    source: 'platform',
    company_enabled: true,
    organization_enabled: id !== 'org-disabled',
    enabled: id !== 'org-disabled',
  }),
)
const update = orgApiMock.orgAdminApi.update
const switchName = '첨부 이미지 설명 허용'
const submitName = '이미지 설명 정책 저장'

describe('organization image description policy', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(modelsApi.list).mockResolvedValue([visionModel])
    getCatalog.mockResolvedValue({ items: catalog, updated_at: makeOrgView().updated_at })
    update.mockResolvedValue(makeOrgView())
  })

  it('opts in with an eligible model, its exact destination and the organization version', async () => {
    vi.mocked(modelsApi.list).mockResolvedValue([
      visionModel,
      { ...visionModel, id: 'org-disabled', display_name: 'Disabled In Org' },
      { ...visionModel, id: 'text-model', display_name: 'Text Only', supports_vision: false },
      {
        ...visionModel,
        id: 'unbound',
        display_name: 'Unbound Vision',
        default_credential_id: null,
      },
      { ...visionModel, id: 'hidden', display_name: 'Hidden Vision', is_visible: false },
      {
        ...visionModel,
        id: 'unpriced',
        display_name: 'Unpriced Vision',
        cost_per_input_token: null,
      },
    ])
    const view = makeOrgView()
    render(<OrgPolicySection view={view} canEdit />)

    expect(modelsApi.list).not.toHaveBeenCalled()
    expect(getCatalog).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('switch', { name: '대화 공개 링크 허용' }))
    await userEvent.click(screen.getByRole('switch', { name: switchName }))
    const option = await screen.findByRole('option', { name: /Vision One/ })
    for (const name of [
      /Text Only/,
      /Unbound Vision/,
      /Hidden Vision/,
      /Unpriced Vision/,
      /Disabled In Org/,
    ]) {
      expect(screen.queryByRole('option', { name })).not.toBeInTheDocument()
    }
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: '이미지 설명 모델' }),
      option,
    )
    const timeout = screen.getByLabelText('요청 제한 시간(초)')
    await userEvent.clear(timeout)
    await userEvent.type(timeout, '61')
    const submit = screen.getByRole('button', { name: submitName })
    expect(submit).toBeDisabled()
    await userEvent.clear(timeout)
    await userEvent.type(timeout, '17')
    const cost = screen.getByLabelText('호출당 최대 비용(USD)')
    await userEvent.clear(cost)
    await userEvent.type(cost, '0.25')
    await userEvent.click(submit)
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        view.id,
        {
          settings: {
            image_description: { ...persistedPolicy, timeout_seconds: 17, max_cost_usd: '0.25' },
          },
        },
        view.updated_at,
      ),
    )
    expect(update).toHaveBeenCalledTimes(1)
  })

  it('can disable an existing policy whose model is no longer selectable', async () => {
    vi.mocked(modelsApi.list).mockResolvedValue([])
    const view = makeOrgView()
    view.settings = { ...view.settings, image_description: persistedPolicy }
    render(<OrgPolicySection view={view} canEdit />)
    const toggle = screen.getByRole('switch', { name: switchName })
    expect(toggle).toBeChecked()
    await userEvent.click(toggle)
    await userEvent.click(screen.getByRole('button', { name: submitName }))
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        view.id,
        { settings: { image_description: null } },
        view.updated_at,
      ),
    )
  })

  it('requires an explicit selection when the registered provider destination changes', async () => {
    const oldUrl = 'https://previous.example.invalid/v1'
    const newUrl = 'https://replacement.example.invalid/v1'
    vi.mocked(modelsApi.list).mockResolvedValue([
      { ...visionModel, provider: 'openai_compatible', base_url: newUrl },
    ])
    const view = makeOrgView()
    view.settings = {
      ...view.settings,
      image_description: { ...persistedPolicy, provider: 'openai_compatible', base_url: oldUrl },
    }
    render(<OrgPolicySection view={view} canEdit />)
    const option = await screen.findByRole('option', { name: /Vision One/ })
    const timeout = screen.getByLabelText('요청 제한 시간(초)')
    await userEvent.clear(timeout)
    await userEvent.type(timeout, '20')
    const submit = screen.getByRole('button', { name: submitName })
    expect(submit).toBeDisabled()
    expect(update).not.toHaveBeenCalled()
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: '이미지 설명 모델' }),
      option,
    )
    await userEvent.click(submit)
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        view.id,
        {
          settings: {
            image_description: {
              ...persistedPolicy,
              provider: 'openai_compatible',
              base_url: newUrl,
              timeout_seconds: 20,
            },
          },
        },
        view.updated_at,
      ),
    )
  })

  it('keeps an existing image policy out of unrelated settings updates', async () => {
    const view = makeOrgView()
    view.settings = { ...view.settings, image_description: persistedPolicy }
    render(<OrgPolicySection view={view} canEdit />)
    await userEvent.click(screen.getByRole('switch', { name: '대화 공개 링크 허용' }))
    await userEvent.click(screen.getByRole('button', { name: '정책 저장' }))
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1))
    const [, patch, version] = update.mock.calls[0]
    expect(patch.settings).not.toHaveProperty('image_description')
    expect(patch.settings.allow_public_share_links).toBe(false)
    expect(version).toBe(view.updated_at)
  })

  it('does not request the model catalog or offer a write to a viewer', () => {
    const view = makeOrgView({ permissions: [] })
    view.settings = { ...view.settings, image_description: persistedPolicy }
    render(<OrgPolicySection view={view} canEdit={false} />)
    expect(screen.queryByRole('button', { name: submitName })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: '이미지 설명 모델' })).not.toBeInTheDocument()
    expect(modelsApi.list).not.toHaveBeenCalled()
    expect(getCatalog).not.toHaveBeenCalled()
    expect(update).not.toHaveBeenCalled()
  })
})

describe('image description model eligibility', () => {
  it.each([
    'https:gateway.example/v1',
    'http://gateway.example/v1',
    'https://gateway.example/v1?token=placeholder',
    'https://user:placeholder@gateway.example/v1',
    'https://gateway.example/v1#fragment',
    'https://gateway.example/v1' + String.fromCharCode(1),
  ])('rejects a malformed or credential-bearing destination (%s)', (base_url) => {
    expect(
      imagePolicyDestination({ ...visionModel, provider: 'openai_compatible', base_url }),
    ).toBeNull()
  })

  it('requires a positive context window and an official destination for an OpenAI profile', () => {
    expect(imagePolicyDestination({ ...visionModel, context_window: -1 })).toBeNull()
    expect(
      imagePolicyDestination({
        ...visionModel,
        provider: 'openai',
        base_url: 'https://different.example/v1',
      }),
    ).toBeNull()
  })
})
