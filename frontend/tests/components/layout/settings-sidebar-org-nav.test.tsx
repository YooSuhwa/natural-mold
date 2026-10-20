import type { AnchorHTMLAttributes, MouseEvent } from 'react'
import { SettingsSidebar } from '@/components/layout/settings-sidebar'
import { SidebarProvider } from '@/components/ui/sidebar'
import { render, screen } from '../../test-utils'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'

const navigationMocks = vi.hoisted(() => ({
  pathname: '/settings',
  refresh: vi.fn(),
}))

const themeMocks = vi.hoisted(() => ({
  setTheme: vi.fn(),
}))

const orgHooks = vi.hoisted(() => ({
  active: vi.fn(),
  info: vi.fn(),
  overview: vi.fn(),
}))

vi.mock('@/lib/hooks/use-organizations', () => ({
  useActiveOrganization: () => orgHooks.active(),
  useOrganizationInfo: () => orgHooks.info(),
  useTenantOverview: () => orgHooks.overview(),
}))

vi.mock('next/link', () => ({
  default: ({
    children,
    href,
    onClick,
    ...props
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a
      href={href}
      onClick={(event: MouseEvent<HTMLAnchorElement>) => {
        event.preventDefault()
        onClick?.(event)
      }}
      {...props}
    >
      {children}
    </a>
  ),
}))

vi.mock('next/image', () => ({
  default: ({ alt, className }: { alt: string; className?: string }) => (
    <span aria-label={alt} className={className} role="img" />
  ),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => navigationMocks.pathname,
  useRouter: () => ({ refresh: navigationMocks.refresh }),
}))

vi.mock('next-themes', () => ({
  useTheme: () => ({
    resolvedTheme: 'light',
    setTheme: themeMocks.setTheme,
  }),
}))

vi.mock('@/components/auth/UserMenu', () => ({
  UserMenu: () => <button type="button">User menu</button>,
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({
    data: {
      email: 'test@example.com',
      id: 'user-1',
      is_super_user: false,
      name: 'Test User',
    },
  }),
}))

vi.mock('@/lib/hooks/useAuth', () => ({
  useLogout: () => ({ mutate: vi.fn() }),
}))

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

describe('SettingsSidebar organization section', () => {
  beforeEach(() => {
    orgHooks.active.mockReturnValue({ activeOrganization: makeOrganization({ roles: [] }) })
    orgHooks.info.mockReturnValue({ data: undefined })
    orgHooks.overview.mockReturnValue({ data: undefined })
  })

  it('shows Roles to company admins via server permissions', () => {
    orgHooks.info.mockReturnValue({ data: { permissions: ['can_manage_members'] } })
    renderSidebar()
    expect(screen.getByRole('link', { name: '역할 및 권한' })).toBeInTheDocument()
  })

  it('hides Roles and the company directory from plain members', () => {
    orgHooks.info.mockReturnValue({ data: { permissions: [] } })
    orgHooks.overview.mockReturnValue({ data: { access: 'member' } })
    renderSidebar()
    expect(screen.getByRole('link', { name: '멤버' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '역할 및 권한' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '회사 조직 목록' })).not.toBeInTheDocument()
  })

  it('keeps the company directory for company auditors', () => {
    orgHooks.overview.mockReturnValue({ data: { access: 'auditor' } })
    renderSidebar()
    expect(screen.getByRole('link', { name: '회사 조직 목록' })).toBeInTheDocument()
  })

  it('shows the company settings screens to company admins (S05–S07)', () => {
    orgHooks.overview.mockReturnValue({ data: { access: 'admin' } })
    renderSidebar()
    for (const name of [
      '회사 조직 목록',
      '회사 구성원',
      '회사 정책',
      '모델과 LLM 설정',
      'SSO와 도메인',
    ]) {
      expect(screen.getByRole('link', { name })).toBeInTheDocument()
    }
  })

  it('fails closed while the company authority is unknown', () => {
    renderSidebar()
    expect(screen.queryByRole('link', { name: '회사 정책' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '회사 조직 목록' })).not.toBeInTheDocument()
  })

  it('shows plain members the company policies only', () => {
    orgHooks.overview.mockReturnValue({ data: { access: 'member' } })
    renderSidebar()
    expect(screen.getByRole('link', { name: '회사 정책' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '회사 구성원' })).not.toBeInTheDocument()
  })

  it('collapses a solo personal company to one "make it a team" entry', () => {
    orgHooks.active.mockReturnValue({
      activeOrganization: makeOrganization({ tenant_kind: 'personal' }),
    })
    orgHooks.info.mockReturnValue({ data: { permissions: ['can_manage_members'] } })
    orgHooks.overview.mockReturnValue({ data: { access: 'admin', active_member_count: 1 } })
    renderSidebar()
    expect(screen.getByRole('link', { name: '사람 초대해서 팀으로 쓰기' })).toHaveAttribute(
      'href',
      '/settings/company/members?invite=1',
    )
    for (const name of ['멤버', '역할 및 권한', '회사 구성원', '회사 정책']) {
      expect(screen.queryByRole('link', { name })).not.toBeInTheDocument()
    }
  })

  it.each([
    ['a personal company with a second member', 'personal', 2],
    ['a shared company with one member', 'company', 1],
  ] as const)('keeps the full administration for %s', (_label, kind, count) => {
    orgHooks.active.mockReturnValue({ activeOrganization: makeOrganization({ tenant_kind: kind }) })
    orgHooks.overview.mockReturnValue({ data: { access: 'admin', active_member_count: count } })
    renderSidebar()
    expect(screen.getByRole('link', { name: '회사 구성원' })).toBeInTheDocument()
    expect(
      screen.queryByRole('link', { name: '사람 초대해서 팀으로 쓰기' }),
    ).not.toBeInTheDocument()
  })

  it('shows the catalog policy only with can_manage_policy', () => {
    orgHooks.info.mockReturnValue({ data: { permissions: ['can_manage_policy'] } })
    const { unmount } = renderSidebar()
    expect(screen.getByRole('link', { name: '카탈로그 정책' })).toHaveAttribute(
      'href',
      '/settings/organization/catalog',
    )
    unmount()
    orgHooks.info.mockReturnValue({ data: { permissions: ['can_manage_members'] } })
    renderSidebar()
    expect(screen.queryByRole('link', { name: '카탈로그 정책' })).not.toBeInTheDocument()
  })
})
