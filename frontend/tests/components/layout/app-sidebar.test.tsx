import { render, screen, userEvent } from '../../test-utils'
import { AppSidebar } from '@/components/layout/app-sidebar'
import { mockAgentSummaryList } from '../../mocks/fixtures'

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

const mockUseAgentSummaries = vi.fn()
const mockUseTriggerSummary = vi.fn()
const mockUseSession = vi.fn()
const sidebarMocks = vi.hoisted(() => {
  const setOpen = vi.fn()
  const toggleSidebar = vi.fn()
  const useSidebar = vi.fn(() => ({
    isMobile: false,
    open: true,
    openMobile: false,
    setOpen,
    setOpenMobile: vi.fn(),
    setSidebarWidth: vi.fn(),
    sidebarWidth: 256,
    state: 'expanded',
    toggleSidebar,
  }))

  return { setOpen, toggleSidebar, useSidebar }
})

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgentSummaries: () => mockUseAgentSummaries(),
}))

vi.mock('@/lib/hooks/use-triggers', () => ({
  useTriggerSummary: () => mockUseTriggerSummary(),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => mockUseSession(),
}))

vi.mock('jotai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('jotai')>()
  return {
    ...actual,
    useAtom: () => [true, vi.fn()],
  }
})

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
      const linkProps = renderProp.props as React.AnchorHTMLAttributes<HTMLAnchorElement>
      return (
        <a {...linkProps} {...props}>
          {children}
        </a>
      )
    }
    return <button {...(props as React.ButtonHTMLAttributes<HTMLButtonElement>)}>{children}</button>
  },
  SidebarMenuItem: ({ children }: { children: React.ReactNode }) => <li>{children}</li>,
  SidebarMenuBadge: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
  SidebarRail: () => <div data-testid="sidebar-rail" />,
  SidebarMenuSub: ({ children }: { children: React.ReactNode }) => <ul>{children}</ul>,
  SidebarMenuSubItem: ({ children }: { children: React.ReactNode }) => <li>{children}</li>,
  SidebarMenuSubButton: ({
    children,
    render: renderProp,
    ...props
  }: {
    children: React.ReactNode
    render?: React.ReactElement
    [key: string]: unknown
  }) => {
    if (renderProp && typeof renderProp === 'object' && 'props' in renderProp) {
      const linkProps = renderProp.props as React.AnchorHTMLAttributes<HTMLAnchorElement>
      return (
        <a {...linkProps} {...props}>
          {children}
        </a>
      )
    }
    return <button {...(props as React.ButtonHTMLAttributes<HTMLButtonElement>)}>{children}</button>
  },
  SidebarSeparator: () => <hr />,
  useSidebar: () => sidebarMocks.useSidebar(),
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

vi.mock('@/components/layout/chat-navigator', () => ({
  ChatNavigator: () => <div data-testid="chat-navigator">에이전트</div>,
}))

describe('AppSidebar', () => {
  beforeEach(() => {
    mockUseAgentSummaries.mockReturnValue({ data: undefined, isLoading: false })
    mockUseTriggerSummary.mockReturnValue({ data: { total_unread: 0, active_count: 0 } })
    mockUseSession.mockReturnValue({
      data: {
        id: 'user-1',
        name: 'Test User',
        email: 'test@example.com',
        is_super_user: false,
      },
    })
    sidebarMocks.setOpen.mockClear()
    sidebarMocks.toggleSidebar.mockClear()
    sidebarMocks.useSidebar.mockReset()
    sidebarMocks.useSidebar.mockReturnValue({
      isMobile: false,
      open: true,
      openMobile: false,
      setOpen: sidebarMocks.setOpen,
      setOpenMobile: vi.fn(),
      setSidebarWidth: vi.fn(),
      sidebarWidth: 256,
      state: 'expanded',
      toggleSidebar: sidebarMocks.toggleSidebar,
    })
  })

  it('renders sidebar with brand', () => {
    render(<AppSidebar />)
    expect(screen.getByText('Moldy')).toBeInTheDocument()
  })

  it('renders navigation items', () => {
    render(<AppSidebar />)
    expect(screen.getByText('에이전트 템플릿')).toBeInTheDocument()
    expect(screen.getByText('마켓플레이스')).toBeInTheDocument()
    expect(screen.getByText('기능')).toBeInTheDocument()
    expect(screen.getByText('도구')).toBeInTheDocument()
    expect(screen.getByText('MCP 서버')).toBeInTheDocument()
    expect(screen.getByText('스킬')).toBeInTheDocument()
  })

  it('keeps schedule unread badge out of the main sidebar', () => {
    mockUseTriggerSummary.mockReturnValue({ data: { total_unread: 3, active_count: 1 } })
    render(<AppSidebar />)
    expect(screen.queryByText('3')).not.toBeInTheDocument()
  })

  it('renders new agent button', () => {
    render(<AppSidebar />)
    expect(screen.getByText('새 에이전트')).toBeInTheDocument()
  })

  it('expands the collapsed sidebar when a rail menu icon is clicked', async () => {
    const user = userEvent.setup()
    sidebarMocks.useSidebar.mockReturnValue({
      isMobile: false,
      open: false,
      openMobile: false,
      setOpen: sidebarMocks.setOpen,
      setOpenMobile: vi.fn(),
      setSidebarWidth: vi.fn(),
      sidebarWidth: 48,
      state: 'collapsed',
      toggleSidebar: sidebarMocks.toggleSidebar,
    })

    render(<AppSidebar />)

    await user.click(screen.getByRole('link', { name: '새 에이전트' }))

    expect(sidebarMocks.setOpen).toHaveBeenCalledWith(true)
  })

  it('expands the collapsed sidebar when a collapsible feature icon is clicked', async () => {
    const user = userEvent.setup()
    sidebarMocks.useSidebar.mockReturnValue({
      isMobile: false,
      open: false,
      openMobile: false,
      setOpen: sidebarMocks.setOpen,
      setOpenMobile: vi.fn(),
      setSidebarWidth: vi.fn(),
      sidebarWidth: 48,
      state: 'collapsed',
      toggleSidebar: sidebarMocks.toggleSidebar,
    })

    render(<AppSidebar />)

    await user.click(screen.getByRole('button', { name: '기능' }))

    expect(sidebarMocks.setOpen).toHaveBeenCalledWith(true)
  })

  it('renders marketplace as a single sidebar link for regular users', () => {
    render(<AppSidebar />)

    expect(screen.getByRole('link', { name: '마켓플레이스' })).toHaveAttribute(
      'href',
      '/marketplace',
    )
    expect(screen.queryByText('둘러보기')).not.toBeInTheDocument()
    expect(screen.queryByText('운영자 관리')).not.toBeInTheDocument()
    expect(screen.queryByText('시스템 자격증명')).not.toBeInTheDocument()
    expect(screen.queryByText('시스템 LLM 설정')).not.toBeInTheDocument()
  })

  it('keeps admin-only links out of the main sidebar for super users', () => {
    mockUseSession.mockReturnValue({
      data: {
        id: 'admin-1',
        name: 'Admin User',
        email: 'admin@example.com',
        is_super_user: true,
      },
    })

    render(<AppSidebar />)

    expect(screen.getByRole('link', { name: '마켓플레이스' })).toHaveAttribute(
      'href',
      '/marketplace',
    )
    expect(screen.queryByText('둘러보기')).not.toBeInTheDocument()
    expect(screen.queryByText('운영자 관리')).not.toBeInTheDocument()
    expect(screen.queryByText('시스템 자격증명')).not.toBeInTheDocument()
    expect(screen.queryByText('시스템 LLM 설정')).not.toBeInTheDocument()
  })

  it('renders the consolidated agent navigator', () => {
    mockUseAgentSummaries.mockReturnValue({
      data: mockAgentSummaryList,
      isLoading: false,
    })
    render(<AppSidebar />)
    expect(screen.getByText('에이전트')).toBeInTheDocument()
  })

  it('places the agent navigator above secondary resource links', () => {
    render(<AppSidebar />)

    const navigator = screen.getByTestId('chat-navigator')
    const templates = screen.getByRole('link', { name: '에이전트 템플릿' })

    expect(navigator.compareDocumentPosition(templates) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it('does not render legacy recent-agent skeletons in the shell', () => {
    mockUseAgentSummaries.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(<AppSidebar />)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBe(0)
  })

  it('renders footer section', () => {
    render(<AppSidebar />)
    expect(screen.getByTestId('sidebar-footer')).toBeInTheDocument()
  })

  it('keeps the agent navigator mounted when empty', () => {
    mockUseAgentSummaries.mockReturnValue({ data: [], isLoading: false })
    render(<AppSidebar />)
    expect(screen.getByText('에이전트')).toBeInTheDocument()
  })
})
