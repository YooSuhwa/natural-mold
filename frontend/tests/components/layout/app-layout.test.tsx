import { render, screen } from '../../test-utils'
import { AppLayout } from '@/components/layout/app-layout'

vi.mock('next/link', () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode
    href: string
    [key: string]: unknown
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}))

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => ({ data: [], isLoading: false }),
}))

vi.mock('@/lib/hooks/use-triggers', () => ({
  useTriggerSummary: () => ({ data: { total_unread: 0, active_count: 0 } }),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({
    data: {
      id: 'user-1',
      name: 'Test User',
      email: 'test@example.com',
      is_super_user: false,
    },
  }),
}))

vi.mock('jotai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('jotai')>()
  return {
    ...actual,
    useAtom: () => [true, vi.fn()],
  }
})

vi.mock('@/components/ui/sidebar', () => ({
  SidebarProvider: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="sidebar-provider">{children}</div>
  ),
  SidebarInset: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="sidebar-inset">{children}</div>
  ),
  Sidebar: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarGroupContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarGroupLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarMenu: ({ children }: { children: React.ReactNode }) => <ul>{children}</ul>,
  SidebarMenuButton: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  SidebarMenuItem: ({ children }: { children: React.ReactNode }) => <li>{children}</li>,
  SidebarMenuBadge: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
  SidebarMenuSub: ({ children }: { children: React.ReactNode }) => <ul>{children}</ul>,
  SidebarMenuSubItem: ({ children }: { children: React.ReactNode }) => <li>{children}</li>,
  SidebarMenuSubButton: ({ children }: { children: React.ReactNode }) => (
    <button>{children}</button>
  ),
  SidebarSeparator: () => <hr />,
  SidebarTrigger: () => <button />,
  useSidebar: () => ({ toggleSidebar: vi.fn(), isMobile: false, state: 'expanded', openMobile: false, setOpenMobile: vi.fn() }),
}))

vi.mock('@/components/ui/separator', () => ({
  Separator: () => <hr />,
}))

vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('@/components/layout/breadcrumb-nav', () => ({
  BreadcrumbNav: () => <nav data-testid="breadcrumb-nav" />,
}))

vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
}))

describe('AppLayout', () => {
  it('renders children', () => {
    render(
      <AppLayout>
        <div>Test content</div>
      </AppLayout>,
    )
    expect(screen.getByText('Test content')).toBeInTheDocument()
  })

  it('renders sidebar provider', () => {
    render(
      <AppLayout>
        <div>Content</div>
      </AppLayout>,
    )
    expect(screen.getByTestId('sidebar-provider')).toBeInTheDocument()
  })
})
