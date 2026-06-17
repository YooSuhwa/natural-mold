'use client'

import { useTranslations } from 'next-intl'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  ArrowLeftIcon,
  BarChart3Icon,
  BrainIcon,
  CalendarClockIcon,
  Code2Icon,
  FilesIcon,
  HistoryIcon,
  KeyRoundIcon,
  MonitorCogIcon,
  ShieldCheckIcon,
  SlidersHorizontalIcon,
  StoreIcon,
  UsersRoundIcon,
  UserIcon,
} from 'lucide-react'

import { SidebarBrandHeader } from '@/components/layout/sidebar-brand-header'
import { SidebarNavSection } from '@/components/layout/sidebar-nav-section'
import type { SidebarNavSectionConfig } from '@/components/layout/sidebar-nav-section'
import { SidebarUtilityFooter } from '@/components/layout/sidebar-utility-footer'
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
  useSidebar,
} from '@/components/ui/sidebar'
import { useSession } from '@/lib/auth/session'
import { useTriggerSummary } from '@/lib/hooks/use-triggers'

export function SettingsSidebar() {
  const pathname = usePathname()
  const { setOpen, state } = useSidebar()
  const tSidebar = useTranslations('sidebar')
  const tSettings = useTranslations('appSettings')
  const { data: user } = useSession()
  const { data: triggerSummary } = useTriggerSummary()

  const baseSections: SidebarNavSectionConfig[] = [
    {
      label: tSettings('nav.account'),
      items: [
        { href: '/settings', label: tSettings('nav.profile'), icon: UserIcon, exact: true },
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
      items: [{ href: '/settings/agent-api', label: tSettings('nav.agentApi'), icon: Code2Icon }],
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
  ]

  const adminSections: SidebarNavSectionConfig[] = user?.is_super_user
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
              href: '/settings/admin-audit',
              label: tSettings('nav.adminAudit'),
              icon: UsersRoundIcon,
            },
          ],
        },
      ]
    : []

  const sections = [...baseSections, ...adminSections]
  const activeMenuClass = 'moldy-sidebar-nav-item'
  const isSidebarCollapsed = state === 'collapsed'

  function expandSidebarFromIcon() {
    if (isSidebarCollapsed) {
      setOpen(true)
    }
  }

  return (
    <Sidebar collapsible="icon" className="moldy-sidebar-rail">
      <SidebarBrandHeader
        brandLabel={tSidebar('brand')}
        homeHref="/"
        toggleLabel={tSidebar('toggleSidebar')}
      />

      <SidebarContent className="min-h-0 px-1">
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu className="gap-1">
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip={tSettings('nav.backToApp')}
                  render={<Link href="/" onClick={expandSidebarFromIcon} />}
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
            <SidebarNavSection
              key={section.label}
              menuClassName={activeMenuClass}
              onNavigate={expandSidebarFromIcon}
              pathname={pathname}
              section={section}
            />
          ))}
        </nav>
      </SidebarContent>

      <SidebarUtilityFooter />
      <SidebarRail />
    </Sidebar>
  )
}
