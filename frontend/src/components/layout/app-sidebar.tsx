'use client'

import { useMemo } from 'react'
import Image from 'next/image'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  HomeIcon,
  WrenchIcon,
  ServerIcon,
  BarChart3Icon,
  BookOpenIcon,
  LayoutTemplateIcon,
  KeyRoundIcon,
  BrainIcon,
  PlusIcon,
  Plug2Icon,
  ShieldIcon,
  SlidersHorizontalIcon,
  ChevronRightIcon,
  StoreIcon,
  ToggleRightIcon,
  ToggleLeftIcon,
  SunIcon,
  MoonIcon,
  MonitorIcon,
} from 'lucide-react'
import { useAtom } from 'jotai'
import { useTheme } from 'next-themes'
import { useTranslations } from 'next-intl'

import { useAgents } from '@/lib/hooks/use-agents'
import { UserMenu } from '@/components/auth/UserMenu'
import { useSession } from '@/lib/auth/session'
import { useLogout } from '@/lib/hooks/useAuth'
import { connectorsExpandedAtom } from '@/lib/stores/sidebar-store'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarSeparator,
  useSidebar,
} from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'

export function AppSidebar() {
  const pathname = usePathname()
  const { data: agents, isLoading } = useAgents()
  const { setTheme, theme } = useTheme()
  const { toggleSidebar } = useSidebar()
  const t = useTranslations('sidebar')
  const { data: user } = useSession()
  const logout = useLogout()

  const [connectorsExpanded, setConnectorsExpanded] = useAtom(connectorsExpandedAtom)

  const buildItems = [
    { label: t('nav.home'), href: '/', icon: HomeIcon },
    { label: t('nav.templates'), href: '/agents/new/template', icon: LayoutTemplateIcon },
  ]

  const connectorChildren = [
    { label: t('nav.tools'), href: '/tools', icon: WrenchIcon },
    { label: t('nav.mcpServers'), href: '/mcp-servers', icon: ServerIcon },
  ]

  const skillsItem = {
    label: t('nav.skills'),
    href: '/skills',
    icon: BookOpenIcon,
    tooltip: t('tooltip.skills'),
  }

  const marketplaceItems = [
    { label: 'Marketplace', href: '/marketplace', icon: StoreIcon },
    ...(user?.is_super_user
      ? [
          {
            label: 'Moderation',
            href: '/marketplace/admin/moderation',
            icon: ShieldIcon,
          },
        ]
      : []),
  ]

  const resourceItems = [
    { label: t('nav.credentials'), href: '/credentials', icon: KeyRoundIcon },
    // System Credentials are operator-managed; only super_user sees the menu.
    // Backend enforces 403 via ``require_super_user`` — this hides the chrome.
    ...(user?.is_super_user
      ? [
          {
            label: t('nav.systemCredentials'),
            href: '/settings/system-credentials',
            icon: ShieldIcon,
          },
          {
            label: t('nav.systemLlm'),
            href: '/settings/system-llm',
            icon: SlidersHorizontalIcon,
          },
        ]
      : []),
    { label: t('nav.models'), href: '/models', icon: BrainIcon },
    { label: t('nav.usage'), href: '/usage', icon: BarChart3Icon },
  ]

  const isConnectorActive = connectorChildren.some((child) =>
    pathname.startsWith(child.href),
  )

  const recentAgents = useMemo(
    () =>
      agents
        ?.slice()
        .sort((a, b) => {
          // Prefer last_used_at (max conversation.updated_at) so chatting with
          // an agent floats it to the top. Fall back to updated_at for agents
          // that have never been used.
          const aTime = new Date(a.last_used_at ?? a.updated_at).getTime()
          const bTime = new Date(b.last_used_at ?? b.updated_at).getTime()
          return bTime - aTime
        })
        .slice(0, 5),
    [agents],
  )

  const activeMenuClass =
    'data-active:bg-emerald-100/50 data-active:font-semibold data-active:text-emerald-800 data-active:hover:bg-emerald-100/70 data-active:hover:text-emerald-900 dark:data-active:bg-emerald-500/15 dark:data-active:text-emerald-300 dark:data-active:hover:bg-emerald-500/20 dark:data-active:hover:text-emerald-200'

  const newAgentButtonClass =
    'h-11 rounded-xl border border-emerald-200/80 bg-emerald-100/50 font-semibold text-emerald-800 hover:border-emerald-300/80 hover:bg-emerald-100/70 hover:text-emerald-900 active:bg-emerald-100/80 active:text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/15 dark:text-emerald-300 dark:hover:border-emerald-500/40 dark:hover:bg-emerald-500/20 dark:hover:text-emerald-200'

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-4 py-4 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:py-2">
        {/* Expanded state: logo + brand + toggle (radio ON) */}
        <div className="flex items-center justify-between group-data-[collapsible=icon]:hidden">
          <Link href="/" className="flex items-center gap-2">
            <Image
              src="/logo.webp"
              alt="Moldy"
              width={32}
              height={32}
              className="size-8 shrink-0 object-contain"
            />
            <span className="text-lg font-bold tracking-tight">{t('brand')}</span>
          </Link>
          <button
            type="button"
            onClick={toggleSidebar}
            className="rounded-md p-1.5 text-primary-strong hover:bg-sidebar-accent transition-colors"
            aria-label={t('toggleSidebar')}
          >
            <ToggleRightIcon className="size-5" />
          </button>
        </div>
        {/* Collapsed state: logo default, toggle on hover */}
        <div className="hidden items-center justify-center group-data-[collapsible=icon]:flex">
          <button
            type="button"
            onClick={toggleSidebar}
            className="group/toggle relative flex size-8 items-center justify-center rounded-md transition-colors hover:bg-sidebar-accent"
            aria-label={t('toggleSidebar')}
          >
            <Image
              src="/logo.webp"
              alt="Moldy"
              width={32}
              height={32}
              className="size-8 object-contain transition-opacity group-hover/toggle:opacity-0"
            />
            <ToggleLeftIcon className="absolute size-5 text-muted-foreground opacity-0 transition-opacity group-hover/toggle:opacity-100" />
          </button>
        </div>
      </SidebarHeader>

      <SidebarContent>
        {/* New Agent Button */}
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip={t('newAgent')}
                  render={<Link href="/agents/new" />}
                  className={newAgentButtonClass}
                >
                  <PlusIcon className="size-4" />
                  <span>{t('newAgent')}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarSeparator />

        {/* Build group — Home, Templates */}
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu className="gap-1">
              {buildItems.map((item) => {
                const isActive =
                  item.href === '/' ? pathname === '/' : pathname.startsWith(item.href)
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      isActive={isActive}
                      tooltip={item.label}
                      render={<Link href={item.href} />}
                      className={activeMenuClass}
                    >
                      <item.icon className="size-4" />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarSeparator />

        {/* Capabilities group — Connectors (collapsible), Skills */}
        <SidebarGroup>
          <SidebarGroupLabel>{t('section.capabilities')}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu className="gap-1">
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={isConnectorActive}
                  tooltip={t('tooltip.connectors')}
                  onClick={() => setConnectorsExpanded(!connectorsExpanded)}
                  aria-expanded={connectorsExpanded}
                  className={activeMenuClass}
                >
                  <Plug2Icon className="size-4" />
                  <span>{t('nav.connectors')}</span>
                  <ChevronRightIcon
                    className={`ml-auto size-4 transition-transform ${
                      connectorsExpanded ? 'rotate-90' : ''
                    }`}
                  />
                </SidebarMenuButton>
                {connectorsExpanded && (
                  <SidebarMenuSub>
                    {connectorChildren.map((child) => {
                      const isActive = pathname.startsWith(child.href)
                      return (
                        <SidebarMenuSubItem key={child.href}>
                          <SidebarMenuSubButton
                            isActive={isActive}
                            render={<Link href={child.href} />}
                            className={activeMenuClass}
                          >
                            <child.icon className="size-4" />
                            <span>{child.label}</span>
                          </SidebarMenuSubButton>
                        </SidebarMenuSubItem>
                      )
                    })}
                  </SidebarMenuSub>
                )}
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={pathname.startsWith(skillsItem.href)}
                  tooltip={skillsItem.tooltip}
                  render={<Link href={skillsItem.href} />}
                  className={activeMenuClass}
                >
                  <skillsItem.icon className="size-4" />
                  <span>{skillsItem.label}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              {marketplaceItems.map((item) => {
                const isActive =
                  item.href === '/marketplace'
                    ? pathname === '/marketplace' ||
                      (pathname.startsWith('/marketplace/') &&
                        !pathname.startsWith('/marketplace/admin'))
                    : pathname.startsWith(item.href)
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      isActive={isActive}
                      tooltip={item.label}
                      render={<Link href={item.href} />}
                      className={activeMenuClass}
                    >
                      <item.icon className="size-4" />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarSeparator />

        {/* Resources group — Credentials, Models, Usage */}
        <SidebarGroup>
          <SidebarGroupLabel>{t('section.resources')}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu className="gap-1">
              {resourceItems.map((item) => {
                const isActive = pathname.startsWith(item.href)
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      isActive={isActive}
                      tooltip={item.label}
                      render={<Link href={item.href} />}
                      className={activeMenuClass}
                    >
                      <item.icon className="size-4" />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* Recent Agents */}
        {(isLoading || (recentAgents && recentAgents.length > 0)) && (
          <>
            <SidebarSeparator />
            <SidebarGroup>
              <SidebarGroupLabel>{t('recentAgents')}</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu className="gap-1">
                  {isLoading
                    ? Array.from({ length: 3 }).map((_, i) => (
                        <li key={i} className="flex h-8 items-center gap-2 rounded-md px-2">
                          <Skeleton className="size-4 rounded-md" />
                          <Skeleton className="h-4 flex-1" />
                        </li>
                      ))
                    : recentAgents?.map((agent) => {
                        const isActive =
                          pathname === `/agents/${agent.id}` ||
                          pathname.startsWith(`/agents/${agent.id}/`)
                        return (
                          <SidebarMenuItem key={agent.id}>
                            <SidebarMenuButton
                              isActive={isActive}
                              tooltip={agent.name}
                              render={<Link href={`/agents/${agent.id}`} />}
                              className={activeMenuClass}
                            >
                              <AgentAvatar imageUrl={agent.image_url} name={agent.name} size="xs" />
                              <span>{agent.name}</span>
                            </SidebarMenuButton>
                          </SidebarMenuItem>
                        )
                      })}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          </>
        )}
      </SidebarContent>

      {/* Theme Toggle */}
      <SidebarFooter>
        <SidebarSeparator />
        <div className="flex items-center justify-center gap-1 px-2 py-1">
          {[
            { value: 'light' as const, icon: SunIcon },
            { value: 'dark' as const, icon: MoonIcon },
            { value: 'system' as const, icon: MonitorIcon },
          ].map(({ value, icon: Icon }) => (
            <button
              key={value}
              type="button"
              onClick={() => setTheme(value)}
              suppressHydrationWarning
              className={`rounded-md p-1.5 transition-colors ${theme === value ? 'bg-sidebar-accent text-sidebar-accent-foreground' : 'text-muted-foreground hover:text-foreground hover:bg-sidebar-accent'}`}
              aria-label={t(`theme.${value}`)}
            >
              <Icon className="size-4" />
            </button>
          ))}
        </div>

        {/* User Profile */}
        <SidebarMenu>
          <SidebarMenuItem>
            {user ? (
              <UserMenu user={user} onLogout={() => logout.mutate()} />
            ) : (
              <div className="flex items-center gap-3 px-2 py-2">
                <Skeleton className="size-8 rounded-lg" />
                <div className="flex-1 space-y-1">
                  <Skeleton className="h-3 w-24" />
                  <Skeleton className="h-3 w-32" />
                </div>
              </div>
            )}
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  )
}
