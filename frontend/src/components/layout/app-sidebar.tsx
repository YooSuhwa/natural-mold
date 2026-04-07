'use client'

import { useMemo } from 'react'
import Image from 'next/image'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import {
  HomeIcon,
  WrenchIcon,
  CpuIcon,
  BarChart3Icon,
  BookOpenIcon,
  LayoutTemplateIcon,
  PlusIcon,
  BotIcon,
  UserIcon,
  SettingsIcon,
  LogOutIcon,
  ChevronsUpDownIcon,
  ToggleRightIcon,
  ToggleLeftIcon,
  SunIcon,
  MoonIcon,
  MonitorIcon,
} from 'lucide-react'
import { useTheme } from 'next-themes'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { useAgents } from '@/lib/hooks/use-agents'
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
  SidebarSeparator,
  useSidebar,
} from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'

export function AppSidebar() {
  const pathname = usePathname()
  const router = useRouter()
  const { data: agents, isLoading } = useAgents()
  const { setTheme, theme } = useTheme()
  const { toggleSidebar } = useSidebar()
  const t = useTranslations('sidebar')

  const navItems = [
    { label: t('nav.home'), href: '/', icon: HomeIcon },
    { label: t('nav.tools'), href: '/tools', icon: WrenchIcon },
    { label: t('nav.skills'), href: '/skills', icon: BookOpenIcon },
    { label: t('nav.models'), href: '/models', icon: CpuIcon },
    { label: t('nav.usage'), href: '/usage', icon: BarChart3Icon },
    { label: t('nav.templates'), href: '/agents/new/template', icon: LayoutTemplateIcon },
  ]

  const recentAgents = useMemo(
    () =>
      agents
        ?.slice()
        .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
        .slice(0, 5),
    [agents],
  )

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-4 py-4 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:py-2">
        {/* Expanded state: logo + brand + toggle (radio ON) */}
        <div className="flex items-center justify-between group-data-[collapsible=icon]:hidden">
          <Link href="/" className="flex items-center gap-2">
            <Image
              src="/logo.png"
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
            className="rounded-md p-1.5 text-primary hover:bg-sidebar-accent transition-colors"
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
              src="/logo.png"
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
                  variant="outline"
                  tooltip={t('newAgent')}
                  render={<Link href="/agents/new" />}
                >
                  <PlusIcon className="size-4" />
                  <span>{t('newAgent')}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarSeparator />

        {/* Main Navigation */}
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {navItems.map((item) => {
                const isActive =
                  item.href === '/' ? pathname === '/' : pathname.startsWith(item.href)
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      isActive={isActive}
                      tooltip={item.label}
                      render={<Link href={item.href} />}
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
                <SidebarMenu>
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
                            >
                              <BotIcon className="size-4" />
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
            <DropdownMenu>
              <DropdownMenuTrigger
                className="w-full rounded-md ring-sidebar-ring outline-hidden focus-visible:ring-2 [&>svg]:size-4 [&>svg]:shrink-0"
                render={
                  <SidebarMenuButton
                    size="lg"
                    className="data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground"
                  />
                }
              >
                <div className="flex size-8 items-center justify-center rounded-lg bg-sidebar-accent">
                  <UserIcon className="size-4 text-sidebar-accent-foreground" />
                </div>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-medium">{t('user.name')}</span>
                  <span className="truncate text-xs text-muted-foreground">{t('user.email')}</span>
                </div>
                <ChevronsUpDownIcon className="ml-auto size-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent side="top" align="start" className="w-[--anchor-width]">
                <DropdownMenuItem onClick={() => router.push('/settings')}>
                  <SettingsIcon />
                  {t('settings')}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={() => {
                    toast.info(t('logoutMessage'))
                    router.push('/')
                  }}
                >
                  <LogOutIcon />
                  {t('logout')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  )
}
