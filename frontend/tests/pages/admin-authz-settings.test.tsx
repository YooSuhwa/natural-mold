import AuthzSyncSettingsPage from '@/app/settings/admin/authz/page'
import { render, screen, waitFor } from '../test-utils'

const session = vi.hoisted(() => ({ is_super_user: false }))
const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }))

vi.mock('next/navigation', () => ({ useRouter: () => router, usePathname: () => '/settings' }))
vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ data: { id: 'u-1', ...session }, isPending: false }),
}))
vi.mock('@/app/settings/_components/settings-shell', () => ({
  SettingsShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/features/platform-authz/components/authz-sync-content', () => ({
  AuthzSyncContent: () => <p>sync content</p>,
}))

describe('/settings/admin/authz (S27)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends a non-super user home without rendering the operator screen', async () => {
    session.is_super_user = false
    render(<AuthzSyncSettingsPage />)
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith('/'))
    expect(screen.queryByText('sync content')).not.toBeInTheDocument()
  })

  it('renders the operator screen for a super user', () => {
    session.is_super_user = true
    render(<AuthzSyncSettingsPage />)
    expect(screen.getByText('sync content')).toBeInTheDocument()
    expect(router.replace).not.toHaveBeenCalled()
  })
})
