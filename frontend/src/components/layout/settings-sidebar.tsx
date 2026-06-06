'use client'

import { useLocale, useTranslations } from 'next-intl'
import { useTheme } from 'next-themes'
import Image from 'next/image'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useState } from 'react'
import {
  ArrowLeftIcon,
  BarChart3Icon,
  BrainIcon,
  CalendarClockIcon,
  CheckIcon,
  Code2Icon,
  FilesIcon,
  Globe2Icon,
  HistoryIcon,
  KeyRoundIcon,
  type LucideIcon,
  MonitorCogIcon,
  MoonIcon,
  ShieldCheckIcon,
  SlidersHorizontalIcon,
  StoreIcon,
  SunIcon,
  ToggleLeftIcon,
  ToggleRightIcon,
  UsersRoundIcon,
  UserIcon,
} from 'lucide-react'

import { UserMenu } from '@/components/auth/UserMenu'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
  useSidebar,
} from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'
import { useSession } from '@/lib/auth/session'
import { useLogout } from '@/lib/hooks/useAuth'
import { useTriggerSummary } from '@/lib/hooks/use-triggers'
import { persistLocaleCookie } from '../../i18n/client-locale'
import { SUPPORTED_LOCALES, isSupportedLocale } from '../../i18n/locales'

type SettingsNavItem = {
  label: string
  href: string
  icon: LucideIcon
  badge?: number
}

type SettingsNavSection = {
  label: string
  items: SettingsNavItem[]
}

function isActivePath(pathname: string, href: string) {
  if (href === '/settings') return pathname === href
  return pathname === href || pathname.startsWith(`${href}/`)
}

export function SettingsSidebar() {
  const pathname = usePathname()
  const router = useRouter()
  const { resolvedTheme, setTheme } = useTheme()
  const locale = useLocale()
  const { toggleSidebar } = useSidebar()
  const tSidebar = useTranslations('sidebar')
  const tSettings = useTranslations('appSettings')
  const { data: user } = useSession()
  const logout = useLogout()
  const { data: triggerSummary } = useTriggerSummary()
  const [languageOpen, setLanguageOpen] = useState(false)

  const sections: SettingsNavSection[] = [
    {
      label: tSettings('nav.account'),
      items: [
        { href: '/settings', label: tSettings('nav.profile'), icon: UserIcon },
        { href: '/settings/memory', label: tSettings('nav.memory'), icon: BrainIcon },
        { href: '/settings/security', label: tSettings('nav.security'), icon: ShieldCheckIcon },
        {
          href: '/settings/appearance',
          label: tSettings('nav.appearance'),
          icon: MonitorCogIcon,
        },
        { href: '/settings/audit', label: tSettings('nav.audit'), icon: HistoryIcon },
      ],
    },
    {
      label: tSettings('nav.api'),
      items: [
        { href: '/settings/agent-api', label: tSettings('nav.agentApi'), icon: Code2Icon },
      ],
    },
    {
      label: tSettings('nav.resources'),
      items: [
        { href: '/settings/artifacts', label: tSettings('nav.artifacts'), icon: FilesIcon },
        { href: '/settings/credentials', label: tSettings('nav.credentials'), icon: KeyRoundIcon },
        { href: '/settings/models', label: tSettings('nav.models'), icon: BrainIcon },
        {
          href: '/settings/schedules',
          label: tSettings('nav.schedules'),
          icon: CalendarClockIcon,
          badge: triggerSummary?.total_unread ?? 0,
        },
        { href: '/settings/usage', label: tSettings('nav.usage'), icon: BarChart3Icon },
      ],
    },
    ...(user?.is_super_user
      ? [
          {
            label: tSettings('nav.admin'),
            items: [
              {
                href: '/settings/marketplace-admin',
                label: tSettings('nav.marketplaceAdmin'),
                icon: StoreIcon,
              },
              {
                href: '/settings/system-credentials',
                label: tSettings('nav.systemCredentials'),
                icon: KeyRoundIcon,
              },
              {
                href: '/settings/system-llm',
                label: tSettings('nav.systemLlm'),
                icon: SlidersHorizontalIcon,
              },
              {
                href: '/settings/admin/audit',
                label: tSettings('nav.adminAudit'),
                icon: UsersRoundIcon,
              },
            ],
          },
        ]
      : []),
  ]

  const activeMenuClass = 'moldy-sidebar-nav-item'
  const isDarkTheme = resolvedTheme === 'dark'
  const themeToggleLabel = isDarkTheme ? tSidebar('theme.light') : tSidebar('theme.dark')
  const currentLocale = isSupportedLocale(locale) ? locale : SUPPORTED_LOCALES[0]
  const languageLabel = tSidebar('language.label')

  function changeLocale(nextLocale: string) {
    setLanguageOpen(false)
    if (!isSupportedLocale(nextLocale) || nextLocale === currentLocale) return
    persistLocaleCookie(nextLocale)
    router.refresh()
  }

  return (
    <Sidebar collapsible="icon" className="moldy-sidebar-rail">
      <SidebarHeader className="px-4 py-4 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:py-2">
        <div className="flex items-center justify-between group-data-[collapsible=icon]:hidden">
          <Link href="/" className="flex items-center gap-2">
            <Image
              src="/logo.webp"
              alt="Moldy"
              width={32}
              height={32}
              className="size-8 shrink-0 object-contain"
            />
            <span className="text-lg font-bold tracking-tight">{tSidebar('brand')}</span>
          </Link>
          <button
            type="button"
            onClick={toggleSidebar}
            className="moldy-sidebar-control rounded-lg border border-transparent p-1.5 text-primary-strong hover:border-sidebar-border"
            aria-label={tSidebar('toggleSidebar')}
          >
            <ToggleRightIcon className="size-5" />
          </button>
        </div>
        <div className="hidden items-center justify-center group-data-[collapsible=icon]:flex">
          <button
            type="button"
            onClick={toggleSidebar}
            className="group/toggle relative flex size-8 items-center justify-center rounded-lg transition-colors hover:bg-sidebar-accent"
            aria-label={tSidebar('toggleSidebar')}
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

      <SidebarContent className="min-h-0 px-1">
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu className="gap-1">
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip={tSettings('nav.backToApp')}
                  render={<Link href="/" />}
                  className={activeMenuClass}
                >
                  <ArrowLeftIcon className="size-4" />
                  <span>{tSettings('nav.backToApp')}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarSeparator />

        <nav aria-label={tSettings('navLabel')}>
          {sections.map((section) => (
            <SidebarGroup key={section.label}>
              <SidebarGroupLabel>{section.label}</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu className="gap-1">
                  {section.items.map((item) => {
                    const active = isActivePath(pathname, item.href)
                    return (
                      <SidebarMenuItem key={item.href}>
                        <SidebarMenuButton
                          isActive={active}
                          tooltip={item.label}
                          render={
                            <Link
                              href={item.href}
                              aria-current={active ? 'page' : undefined}
                            />
                          }
                          className={activeMenuClass}
                        >
                          <item.icon className="size-4" />
                          <span>{item.label}</span>
                        </SidebarMenuButton>
                        {item.badge ? (
                          <SidebarMenuBadge>
                            {item.badge > 99 ? '99+' : item.badge}
                          </SidebarMenuBadge>
                        ) : null}
                      </SidebarMenuItem>
                    )
                  })}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          ))}
        </nav>
      </SidebarContent>

      <SidebarFooter className="shrink-0 bg-transparent px-3 pb-3">
        <SidebarSeparator />
        <div className="flex items-center justify-center gap-1 px-2 py-1 group-data-[collapsible=icon]:hidden">
          <button
            type="button"
            onClick={() => setTheme(isDarkTheme ? 'light' : 'dark')}
            suppressHydrationWarning
            className="moldy-sidebar-control inline-flex h-7 items-center justify-center rounded-lg px-1.5"
            aria-label={themeToggleLabel}
            title={themeToggleLabel}
          >
            {isDarkTheme ? <SunIcon className="size-4" /> : <MoonIcon className="size-4" />}
          </button>
          <DropdownMenu open={languageOpen} onOpenChange={setLanguageOpen}>
            <DropdownMenuTrigger
              render={<button type="button" />}
              className="moldy-sidebar-control inline-flex h-7 items-center gap-1 rounded-lg px-1.5 text-xs font-semibold data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground"
              aria-label={languageLabel}
              title={languageLabel}
            >
              <Globe2Icon className="size-4" />
              <span>{currentLocale.toUpperCase()}</span>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="center" className="w-36">
              {SUPPORTED_LOCALES.map((option) => (
                <DropdownMenuItem
                  key={option}
                  onClick={() => changeLocale(option)}
                  className="justify-between"
                >
                  <span>{tSidebar(`language.${option}`)}</span>
                  {option === currentLocale ? (
                    <CheckIcon className="size-4 text-primary-strong" />
                  ) : (
                    <span className="size-4" />
                  )}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <SidebarMenu>
          <SidebarMenuItem>
            {user ? (
              <UserMenu user={user} onLogout={() => logout.mutate()} />
            ) : (
              <div className="flex items-center gap-3 px-2 py-2 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:gap-0 group-data-[collapsible=icon]:px-0">
                <Skeleton className="size-8 rounded-lg" />
                <div className="flex-1 space-y-1 group-data-[collapsible=icon]:hidden">
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
