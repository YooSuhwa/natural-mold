import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from 'react'
import { SettingsSidebar } from '@/components/layout/settings-sidebar'
import { SidebarProvider, useSidebar } from '@/components/ui/sidebar'
import { render, screen, userEvent } from '../../test-utils'

const navigationMocks = vi.hoisted(() => ({
  pathname: '/settings',
  refresh: vi.fn(),
}))

const themeMocks = vi.hoisted(() => ({
  setTheme: vi.fn(),
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
  default: ({
    alt,
    className,
  }: {
    alt: string
    className?: string
  }) => <span aria-label={alt} className={className} role="img" />,
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

function SidebarStateProbe() {
  const { state } = useSidebar()
  return <output data-testid="settings-sidebar-state">{state}</output>
}

function SettingsSidebarHarness({ children }: { children?: ReactNode }) {
  return (
    <SidebarProvider defaultOpen={false}>
      <SettingsSidebar />
      <SidebarStateProbe />
      {children}
    </SidebarProvider>
  )
}

describe('SettingsSidebar collapsed rail behavior', () => {
  beforeEach(() => {
    navigationMocks.pathname = '/settings'
    navigationMocks.refresh.mockClear()
    themeMocks.setTheme.mockClear()
  })

  it('expands the collapsed settings rail when a settings nav icon is clicked', async () => {
    const user = userEvent.setup()
    render(<SettingsSidebarHarness />)

    expect(screen.getByTestId('settings-sidebar-state')).toHaveTextContent('collapsed')

    await user.click(screen.getByRole('link', { name: '프로필' }))

    expect(screen.getByTestId('settings-sidebar-state')).toHaveTextContent('expanded')
  })

  it('expands the collapsed settings rail when the back-to-app icon is clicked', async () => {
    const user = userEvent.setup()
    render(<SettingsSidebarHarness />)

    expect(screen.getByTestId('settings-sidebar-state')).toHaveTextContent('collapsed')

    await user.click(screen.getByRole('link', { name: '앱으로 돌아가기' }))

    expect(screen.getByTestId('settings-sidebar-state')).toHaveTextContent('expanded')
  })
})
