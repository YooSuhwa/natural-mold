import { render, screen } from '../test-utils'
import MarketplaceAdminPage from '@/app/settings/marketplace-admin/page'
import SystemCredentialsPage from '@/app/settings/system-credentials/page'
import SystemLlmSettingsPage from '@/app/settings/system-llm/page'
import type { Credential, CredentialDefinition } from '@/lib/types/credential'
import type { SystemLlmSettingOut } from '@/lib/types/system-llm-setting'

vi.mock('@/components/credential/credential-create-modal', () => ({
  CredentialCreateModal: () => null,
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ data: { id: 'user-1', is_super_user: true }, isPending: false }),
}))

const credential: Credential = {
  id: 'cred-openrouter-uuid',
  user_id: '',
  definition_key: 'openrouter',
  name: 'OpenRouter 이미지 키',
  field_keys: ['base_url', 'api_key'],
  is_shared: false,
  is_system: true,
  status: 'active',
  key_id: 'key-1',
  last_used_at: null,
  last_tested_at: null,
  last_test_result: null,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-02T00:00:00Z',
}

const definitions: CredentialDefinition[] = [
  {
    key: 'openrouter',
    display_name: 'OpenRouter',
    category: 'llm',
    extends: [],
    properties: [],
    has_test: true,
    has_oauth: false,
  },
]

const systemLlmSettings: SystemLlmSettingOut[] = [
  {
    role: 'image',
    credential_id: credential.id,
    credential_name: credential.name,
    provider: 'openrouter',
    base_url: 'https://openrouter.ai/api/v1',
    model_name: 'openai/gpt-5.4-image-2',
    configured: true,
    updated_at: '2026-05-02T00:00:00Z',
  },
]

vi.mock('@/lib/hooks/use-credentials', () => ({
  useSystemCredentials: () => ({ data: [credential], isLoading: false }),
  useCredentialTypes: () => ({ data: definitions }),
  useDeleteSystemCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

vi.mock('@/lib/hooks/use-system-llm-settings', () => ({
  useSystemLlmSettings: () => ({ data: systemLlmSettings, isLoading: false }),
  useUpdateSystemLlmSetting: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

vi.mock('@/lib/hooks/use-marketplace', () => ({
  useAdminSetListed: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDisableItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useKSkillSyncStatus: () => ({
    data: { count: 0, last_updated_at: null },
  }),
  useModerationQueue: () => ({ data: [], isLoading: false }),
}))

vi.mock('@/lib/hooks/use-models', () => ({
  useDiscoverModels: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
  }),
}))

describe('admin settings pages', () => {
  it('renders system credentials with Korean operator copy', () => {
    render(<SystemCredentialsPage />)

    expect(screen.getByRole('heading', { name: '시스템 자격증명' })).toBeInTheDocument()
    expect(screen.getByText('운영자 전용')).toBeInTheDocument()
    expect(screen.getByText(/운영자 계정으로 비용이 청구/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /시스템 자격증명 추가/ })).toBeInTheDocument()
    expect(
      screen.getAllByText((_, node) => node?.textContent?.includes('2개 필드') ?? false).length,
    ).toBeGreaterThanOrEqual(1)
  })

  it('renders system LLM settings with readable credential and model names first', () => {
    render(<SystemLlmSettingsPage />)

    expect(screen.getAllByText('OpenRouter 이미지 키').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('openai/gpt-5.4-image-2').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('제공자')).toBeInTheDocument()
    expect(screen.getByText('openrouter')).toBeInTheDocument()
    expect(screen.queryByText('cred-openrouter-uuid')).not.toBeInTheDocument()
  })

  it('renders marketplace moderation inside the settings admin area', () => {
    render(<MarketplaceAdminPage />)

    expect(screen.getByRole('heading', { name: '마켓플레이스 운영' })).toBeInTheDocument()
    expect(screen.getByText('처리 대기 항목이 없어요')).toBeInTheDocument()
    expect(screen.getByText('k-skill 동기화 상태')).toBeInTheDocument()
  })
})
