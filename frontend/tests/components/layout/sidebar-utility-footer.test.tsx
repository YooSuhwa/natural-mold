import { SidebarUtilityFooter } from '@/components/layout/sidebar-utility-footer'
import { SidebarProvider } from '@/components/ui/sidebar'
import { render, screen, userEvent } from '../../test-utils'

const navigationMocks = vi.hoisted(() => ({
  refresh: vi.fn(),
}))

const themeMocks = vi.hoisted(() => ({
  setTheme: vi.fn(),
}))

const localeMocks = vi.hoisted(() => ({
  locale: 'ko',
}))

const logoutMocks = vi.hoisted(() => ({
  mutate: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: navigationMocks.refresh }),
}))

vi.mock('next-themes', () => ({
  useTheme: () => ({
    resolvedTheme: 'light',
    setTheme: themeMocks.setTheme,
  }),
}))

vi.mock('next-intl', async (importOriginal) => {
  const actual = await importOriginal<typeof import('next-intl')>()
  return {
    ...actual,
    useLocale: () => localeMocks.locale,
  }
})

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
  useLogout: () => logoutMocks,
}))

describe('SidebarUtilityFooter', () => {
  beforeEach(() => {
    navigationMocks.refresh.mockClear()
    themeMocks.setTheme.mockClear()
    logoutMocks.mutate.mockClear()
    document.cookie = 'NEXT_LOCALE=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT'
  })

  it('renders the shared theme, language, and user controls', async () => {
    const user = userEvent.setup()

    render(
      <SidebarProvider>
        <SidebarUtilityFooter />
      </SidebarProvider>,
    )

    await user.click(screen.getByRole('button', { name: '다크 모드' }))

    expect(themeMocks.setTheme).toHaveBeenCalledWith('dark')
    expect(screen.getByRole('button', { name: '언어' })).toHaveTextContent('KO')
    expect(screen.getByRole('button', { name: 'User menu' })).toBeInTheDocument()
  })
})
