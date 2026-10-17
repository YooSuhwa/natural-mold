import type { AnchorHTMLAttributes } from 'react'
import { SettingsSidebar } from '@/components/layout/settings-sidebar'
import { SidebarProvider } from '@/components/ui/sidebar'
import { render, screen } from '../../test-utils'

const access = vi.hoisted(() => ({ allowed: false }))

vi.mock('@/lib/hooks/use-platform-tenants', () => ({
  usePlatformOperatorAccess: () => ({ pending: false, allowed: access.allowed }),
}))
vi.mock('@/lib/hooks/use-organizations', () => ({
  useActiveOrganization: () => ({ activeOrganization: null }),
  useOrganizationInfo: () => ({ data: undefined }),
  useTenantOverview: () => ({ data: undefined }),
}))
vi.mock('next/link', () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}))
vi.mock('next/image', () => ({ default: () => <span /> }))
vi.mock('next/navigation', () => ({
  usePathname: () => '/settings',
  useRouter: () => ({ refresh: vi.fn() }),
}))
vi.mock('next-themes', () => ({ useTheme: () => ({ resolvedTheme: 'light', setTheme: vi.fn() }) }))
vi.mock('@/components/auth/UserMenu', () => ({ UserMenu: () => null }))
vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({
    data: { id: 'u-1', email: 'op@example.com', name: 'Op', is_super_user: true },
  }),
}))
vi.mock('@/lib/hooks/useAuth', () => ({ useLogout: () => ({ mutate: vi.fn() }) }))
vi.mock('@/lib/hooks/use-triggers', () => ({
  useTriggerSummary: () => ({ data: { active_count: 0, total_unread: 0 } }),
}))

function renderSidebar() {
  return render(
    <SidebarProvider defaultOpen>
      <SettingsSidebar />
    </SidebarProvider>,
  )
}

describe('SettingsSidebar platform operator entry (S03, 13.16)', () => {
  it('lists the company directory for the SaaS platform operator', () => {
    access.allowed = true
    renderSidebar()
    expect(screen.getByRole('link', { name: '회사 목록' })).toHaveAttribute(
      'href',
      '/settings/admin/tenants',
    )
  })

  it('hides it for a super user outside SaaS mode', () => {
    access.allowed = false
    renderSidebar()
    expect(screen.queryByRole('link', { name: '회사 목록' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '권한 동기화' })).toBeInTheDocument()
  })
})
