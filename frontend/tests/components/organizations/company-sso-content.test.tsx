import { render, screen, userEvent, waitFor } from '../../test-utils'
import { CompanySsoContent } from '@/features/organizations/components/company-sso-content'
import { ApiError } from '@/lib/api/client'
import type { SsoConnection } from '@/lib/types/sso'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from './org-admin-mocks'

vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const sso = vi.hoisted(() => ({
  tenantSsoApi: {
    listDomains: vi.fn(),
    addDomain: vi.fn(),
    verifyDomain: vi.fn(),
    removeDomain: vi.fn(),
    listConnections: vi.fn(),
    createConnection: vi.fn(),
    testConnection: vi.fn(),
    activateConnection: vi.fn(),
    deleteConnection: vi.fn(),
    getPolicy: vi.fn(),
    putPolicy: vi.fn(),
  },
}))
vi.mock('@/lib/api/sso', () => sso)

const connection = (overrides: Partial<SsoConnection> = {}): SsoConnection => ({
  id: 'c1',
  protocol: 'oidc',
  status: 'draft',
  issuer: 'https://login.example.test',
  client_id: 'moldy',
  has_secret: true,
  scopes: ['openid'],
  server_url: null,
  start_tls: false,
  bind_dn: null,
  user_base_dn: null,
  user_filter: null,
  jit_enabled: false,
  default_role: 'member',
  tested_at: null,
  redirect_uri: 'http://api/auth/callback/c1',
  ...overrides,
})

describe('CompanySsoContent (S30)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    orgApiMock.meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    sso.tenantSsoApi.listDomains.mockResolvedValue({
      items: [
        {
          id: 'd1',
          domain: 'gana.example',
          status: 'pending',
          txt_record: 'moldy-verify=7f3a',
          verified_at: null,
          created_at: '2026-10-01T00:00:00Z',
        },
      ],
    })
    sso.tenantSsoApi.listConnections.mockResolvedValue({ items: [connection()] })
    sso.tenantSsoApi.getPolicy.mockResolvedValue({
      mode: 'off',
      break_glass_user_ids: [],
      password_members: 128,
    })
  })

  it('shows the TXT record to publish and verifies the domain', async () => {
    sso.tenantSsoApi.verifyDomain.mockResolvedValue({})
    render(<CompanySsoContent />)
    expect(await screen.findByText('moldy-verify=7f3a')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '확인' }))
    await waitFor(() =>
      expect(sso.tenantSsoApi.verifyDomain).toHaveBeenCalledWith(expect.any(String), 'd1'),
    )
  })

  it('keeps activation disabled until a test login passes and shows the redirect URI', async () => {
    render(<CompanySsoContent />)
    expect(await screen.findByText('http://api/auth/callback/c1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '활성화' })).toBeDisabled()
    expect(screen.getByText('초안')).toBeInTheDocument()
  })

  it('opens the IdP test login in a new window', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    sso.tenantSsoApi.testConnection.mockResolvedValue({
      authorize_url: 'https://login.example.test/authorize?x=1',
      result: null,
      connection: connection(),
    })
    render(<CompanySsoContent />)
    await userEvent.click(await screen.findByRole('button', { name: '시험 로그인' }))
    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        'https://login.example.test/authorize?x=1',
        '_blank',
        'noopener,noreferrer',
      ),
    )
  })

  it('warns how many password members move to the IdP before saving SSO required', async () => {
    sso.tenantSsoApi.putPolicy.mockResolvedValue({
      mode: 'required',
      break_glass_user_ids: ['u1'],
      password_members: 128,
    })
    render(<CompanySsoContent />)
    await userEvent.click(await screen.findByRole('radio', { name: '필수' }))
    expect(screen.getByTestId('sso-required-notice')).toHaveTextContent('128명')
    await userEvent.type(screen.getByLabelText('비상 관리자 사용자 ID (최대 2명)'), 'u1')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(sso.tenantSsoApi.putPolicy).toHaveBeenCalledWith(expect.any(String), 'required', [
        'u1',
      ]),
    )
  })

  it('tells non-admins the page is for company administrators', async () => {
    sso.tenantSsoApi.listDomains.mockRejectedValue(new ApiError(403, 'permission_required', 'x'))
    render(<CompanySsoContent />)
    expect(await screen.findByText('회사 관리자만 볼 수 있습니다')).toBeInTheDocument()
  })
})
