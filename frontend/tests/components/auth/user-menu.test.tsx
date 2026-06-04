import { render, screen, userEvent } from '../../test-utils'
import { UserMenu } from '@/components/auth/UserMenu'
import type { User } from '@/lib/types/user'

const push = vi.fn()
const logout = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push,
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
}))

vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({
    children,
    render: renderProp,
    className,
    'aria-label': ariaLabel,
    title,
  }: {
    children: React.ReactNode
    render?: React.ReactElement
    className?: string
    'aria-label'?: string
    title?: string
  }) => (
    <button className={className} aria-label={ariaLabel} title={title}>
      {renderProp}
      {children}
    </button>
  ),
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({
    children,
    onClick,
    className,
  }: {
    children: React.ReactNode
    onClick?: React.MouseEventHandler<HTMLButtonElement>
    className?: string
  }) => (
    <button className={className} onClick={onClick}>
      {children}
    </button>
  ),
  DropdownMenuSeparator: () => <hr />,
}))

vi.mock('@/components/ui/sidebar', () => ({
  SidebarMenuButton: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <span className={className}>{children}</span>
  ),
}))

const user: User = {
  id: 'user-1',
  name: 'Test User',
  display_name: '체스터',
  avatar_mode: 'initials',
  avatar_initials: '체',
  avatar_color: 'violet',
  email: 'test@example.com',
  is_super_user: false,
  created_at: '2026-05-01T00:00:00Z',
  last_login_at: null,
}

describe('UserMenu', () => {
  beforeEach(() => {
    push.mockClear()
    logout.mockClear()
  })

  it('keeps only settings and logout actions', () => {
    render(<UserMenu user={user} onLogout={logout} />)

    expect(screen.getByText('체스터')).toBeInTheDocument()
    expect(screen.getByLabelText('체스터 프로필 아이콘')).toHaveTextContent('체')
    expect(screen.getByRole('button', { name: /설정/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /로그아웃/ })).toBeInTheDocument()
    expect(screen.queryByText('API 키 관리')).not.toBeInTheDocument()
    expect(screen.queryByText('프로필 설정')).not.toBeInTheDocument()
  })

  it('opens settings from the user menu', async () => {
    render(<UserMenu user={user} onLogout={logout} />)

    await userEvent.click(screen.getByRole('button', { name: /설정/ }))

    expect(push).toHaveBeenCalledWith('/settings')
  })

  it('keeps logout behavior', async () => {
    render(<UserMenu user={user} onLogout={logout} />)

    await userEvent.click(screen.getByRole('button', { name: /로그아웃/ }))

    expect(logout).toHaveBeenCalledTimes(1)
  })
})
