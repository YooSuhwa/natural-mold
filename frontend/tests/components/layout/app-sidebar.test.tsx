import { render, screen } from '../../test-utils'
import { AppSidebar } from '@/components/layout/app-sidebar'
import { mockAgentList } from '../../mocks/fixtures'

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

vi.mock('next/navigation', () => ({
  usePathname: () => '/',
}))

const mockUseAgents = vi.fn()

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => mockUseAgents(),
}))

// Mock the sidebar UI components to avoid base-ui complexity
vi.mock('@/components/ui/sidebar', () => ({
  Sidebar: ({ children, ...props }: { children: React.ReactNode }) => (
    <aside data-testid="sidebar" {...props}>
      {children}
    </aside>
  ),
  SidebarContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="sidebar-content">{children}</div>
  ),
  SidebarFooter: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="sidebar-footer">{children}</div>
  ),
  SidebarGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarGroupContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarGroupLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarHeader: ({ children, ...props }: { children: React.ReactNode }) => (
    <div {...props}>{children}</div>
  ),
  SidebarMenu: ({ children }: { children: React.ReactNode }) => <ul>{children}</ul>,
  SidebarMenuButton: ({
    children,
    render: renderProp,
    ...props
  }: {
    children: React.ReactNode
    render?: React.ReactElement
    [key: string]: unknown
  }) => {
    if (renderProp && typeof renderProp === 'object' && 'props' in renderProp) {
      const linkProps = renderProp.props as Record<string, unknown>
      return (
        <a href={linkProps.href as string} {...props}>
          {children}
        </a>
      )
    }
    return <button {...(props as React.ButtonHTMLAttributes<HTMLButtonElement>)}>{children}</button>
  },
  SidebarMenuItem: ({ children }: { children: React.ReactNode }) => <li>{children}</li>,
  SidebarSeparator: () => <hr />,
}))

vi.mock('@/components/ui/skeleton', () => ({
  Skeleton: ({ className }: { className?: string }) => (
    <div data-slot="skeleton" className={className} />
  ),
}))

vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({
    children,
    render: renderProp,
  }: {
    children: React.ReactNode
    render?: React.ReactElement
  }) => <div>{renderProp || children}</div>,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
}))

describe('AppSidebar', () => {
  beforeEach(() => {
    mockUseAgents.mockReturnValue({ data: undefined, isLoading: false })
  })

  it('renders sidebar with brand', () => {
    render(<AppSidebar />)
    expect(screen.getByText('Moldy')).toBeInTheDocument()
    expect(screen.getByText('M')).toBeInTheDocument()
  })

  it('renders navigation items', () => {
    render(<AppSidebar />)
    expect(screen.getByText('홈')).toBeInTheDocument()
    expect(screen.getByText('도구')).toBeInTheDocument()
    expect(screen.getByText('모델')).toBeInTheDocument()
    expect(screen.getByText('사용량')).toBeInTheDocument()
  })

  it('renders new agent button', () => {
    render(<AppSidebar />)
    expect(screen.getByText('새 에이전트')).toBeInTheDocument()
  })

  it('shows recent agents when loaded', () => {
    mockUseAgents.mockReturnValue({
      data: mockAgentList,
      isLoading: false,
    })
    render(<AppSidebar />)
    expect(screen.getByText('최근 에이전트')).toBeInTheDocument()
    expect(screen.getByText('Test Agent')).toBeInTheDocument()
    expect(screen.getByText('Second Agent')).toBeInTheDocument()
  })

  it('shows loading skeletons for recent agents', () => {
    mockUseAgents.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(<AppSidebar />)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('renders footer section', () => {
    render(<AppSidebar />)
    expect(screen.getByTestId('sidebar-footer')).toBeInTheDocument()
  })

  it('does not show recent agents section when empty', () => {
    mockUseAgents.mockReturnValue({ data: [], isLoading: false })
    render(<AppSidebar />)
    expect(screen.queryByText('최근 에이전트')).not.toBeInTheDocument()
  })
})
