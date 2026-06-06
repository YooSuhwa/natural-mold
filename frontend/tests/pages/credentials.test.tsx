import { render, screen, userEvent } from '../test-utils'
import CredentialsPage from '@/app/settings/credentials/page'
import type { Credential, CredentialDefinition } from '@/lib/types/credential'

const mockUseCredentials = vi.fn()
const mockUseCredentialTypes = vi.fn()

vi.mock('@/lib/hooks/use-credentials', () => ({
  useCredentials: () => mockUseCredentials(),
  useCredentialTypes: () => mockUseCredentialTypes(),
}))

vi.mock('@/components/credential/credential-create-modal', () => ({
  CredentialCreateModal: () => null,
}))

vi.mock('@/components/credential/credential-detail-dialog', () => ({
  CredentialDetailDialog: () => null,
}))

const definition: CredentialDefinition = {
  key: 'openai',
  display_name: 'OpenAI',
  icon_id: 'openai',
  documentation_url: null,
  category: 'llm',
  extends: [],
  properties: [],
  has_test: true,
  has_oauth: false,
}

const credential: Credential = {
  id: 'credential-1',
  user_id: 'user-1',
  definition_key: 'openai',
  name: '운영용 OpenAI',
  field_keys: ['api_key', 'organization'],
  is_shared: false,
  status: 'active',
  key_id: 'primary',
  last_used_at: '2026-05-01T00:00:00Z',
  last_tested_at: '2026-05-02T00:00:00Z',
  last_test_result: null,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-02T00:00:00Z',
}

describe('CredentialsPage', () => {
  beforeEach(() => {
    mockUseCredentials.mockReturnValue({ data: [credential], isLoading: false })
    mockUseCredentialTypes.mockReturnValue({ data: [definition] })
  })

  it('uses a tabbed card panel instead of the old credential table', () => {
    render(<CredentialsPage />)

    expect(screen.getByRole('tab', { name: '전체 1개' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('자격증명 검색')).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '이름' })).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByText('운영용 OpenAI')).toBeInTheDocument()
    expect(screen.getByText('2개 필드')).toBeInTheDocument()
  })

  it('renders credentials as quiet management cards by default', () => {
    render(<CredentialsPage />)

    const card = screen.getByText('운영용 OpenAI').closest('button')
    expect(card).toHaveClass('moldy-resource-card')
    expect(card?.className).toMatch(/\bmoldy-tone-card-mint\b/)
    expect(screen.getByText('OpenAI')).toBeInTheDocument()
  })

  it('filters credentials from the shared status tabs', async () => {
    const user = userEvent.setup()
    render(<CredentialsPage />)

    await user.click(screen.getByRole('tab', { name: /^활성$/ }))

    expect(screen.getByRole('tab', { name: '활성 1개' })).toHaveAttribute('aria-selected', 'true')
  })
})
