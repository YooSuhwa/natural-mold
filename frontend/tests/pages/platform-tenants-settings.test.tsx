import PlatformTenantsPage from '@/app/settings/admin/tenants/page'
import { platformTenantsApi } from '@/lib/api/platform-tenants'
import { ApiError } from '@/lib/api/errors'
import { render, screen } from '../test-utils'

vi.mock('@/lib/api/platform-tenants', async () => {
  const { platformApiMock } = await import('../components/platform-tenants/fixtures')
  return { platformTenantsApi: platformApiMock(), tenantVersionHeader: vi.fn() }
})
vi.mock('@/app/settings/_components/settings-shell', () => ({
  SettingsShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/features/platform-tenants/components/tenant-list-content', () => ({
  TenantListContent: () => <p>company list</p>,
}))

const api = vi.mocked(platformTenantsApi)

function installation(enabled: boolean) {
  return {
    deployment_mode: enabled ? ('multi_tenant' as const) : ('single_tenant' as const),
    org_multi_enabled: true,
    platform_tenant_management_enabled: enabled,
  }
}

describe('/settings/admin/tenants (S03 gate, 13.16)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the company list for the SaaS platform operator', async () => {
    api.installation.mockResolvedValue(installation(true))
    render(<PlatformTenantsPage />)
    expect(await screen.findByText('company list')).toBeInTheDocument()
  })

  it('shows a loading state while the operator capability is unknown', () => {
    api.installation.mockReturnValue(new Promise(() => undefined))
    render(<PlatformTenantsPage />)
    expect(screen.getByRole('status')).toHaveTextContent('권한을 확인하는 중')
    expect(screen.queryByText('company list')).not.toBeInTheDocument()
  })

  it('hides the screen outside SaaS mode or for non-operators', async () => {
    api.installation.mockResolvedValue(installation(false))
    render(<PlatformTenantsPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('이 화면을 찾을 수 없어요')
    expect(screen.queryByText('company list')).not.toBeInTheDocument()
  })

  it('fails closed when the capability cannot be read', async () => {
    api.installation.mockRejectedValue(new ApiError(503, 'authz_unavailable', 'down'))
    render(<PlatformTenantsPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('플랫폼 운영 화면은')
    expect(screen.queryByText('company list')).not.toBeInTheDocument()
  })
})
