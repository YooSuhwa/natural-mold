'use client'

import { useCallback } from 'react'
import { useAtom } from 'jotai'
import {
  BookOpenIcon,
  InboxIcon,
  LayoutTemplateIcon,
  Plug2Icon,
  PlusIcon,
  ServerIcon,
  StoreIcon,
  WrenchIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

import {
  AppSidebarCollapsibleNavItem,
  type AppSidebarNavChild,
} from '@/components/layout/app-sidebar-collapsible-nav-item'
import { ChatNavigator } from '@/components/layout/chat-navigator'
import { OrganizationSwitcher } from '@/components/layout/organization-switcher'
import { SidebarBrandHeader } from '@/components/layout/sidebar-brand-header'
import { SidebarUtilityFooter } from '@/components/layout/sidebar-utility-footer'
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
  useSidebar,
} from '@/components/ui/sidebar'
import { usePendingApprovalCount } from '@/lib/hooks/use-approvals'
import { featuresExpandedAtom } from '@/lib/stores/sidebar-store'

export function AppSidebar() {
  const pathname = usePathname()
  const { setOpen, state } = useSidebar()
  const t = useTranslations('sidebar')

  const [featuresExpanded, setFeaturesExpanded] = useAtom(featuresExpandedAtom)
  const pendingApprovals = usePendingApprovalCount().data?.count ?? 0

  const buildItems = [
    { label: t('nav.templates'), href: '/agents/new/template', icon: LayoutTemplateIcon, badge: 0 },
    { label: t('nav.marketplace'), href: '/marketplace', icon: StoreIcon, badge: 0 },
    // 승인함 (plan 17.2): requests waiting for my decision from every run.
    { label: t('nav.approvals'), href: '/approvals', icon: InboxIcon, badge: pendingApprovals },
  ]

  const featureChildren: AppSidebarNavChild[] = [
    { label: t('nav.tools'), href: '/tools', icon: WrenchIcon },
    { label: t('nav.mcpServers'), href: '/mcp-servers', icon: ServerIcon },
    { label: t('nav.skills'), href: '/skills', icon: BookOpenIcon },
  ].map((c) => ({ ...c, isActive: pathname.startsWith(c.href) }))

  const isFeatureActive = featureChildren.some((child) => child.isActive)

  const activeMenuClass = 'moldy-sidebar-nav-item'

  const newAgentButtonClass = 'moldy-sidebar-new-agent'

  const expandSidebarFromIcon = useCallback(() => {
    if (state === 'collapsed') {
      setOpen(true)
    }
  }, [setOpen, state])

  return (
    <Sidebar collapsible="icon" className="moldy-sidebar-rail">
      <SidebarBrandHeader brandLabel={t('brand')} homeHref="/" toggleLabel={t('toggleSidebar')} />

      <SidebarContent className="min-h-0 px-1">
        <OrganizationSwitcher />

        {/* New Agent Button */}
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip={t('newAgent')}
                  render={<Link href="/agents/new" onClick={expandSidebarFromIcon} />}
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
        <ChatNavigator />

        <SidebarSeparator />

        {/* Build group — Templates, Marketplace, Approvals */}
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
                      render={<Link href={item.href} onClick={expandSidebarFromIcon} />}
                      className={activeMenuClass}
                    >
                      <item.icon className="size-4" />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                    {item.badge ? (
                      <SidebarMenuBadge>{item.badge > 99 ? '99+' : item.badge}</SidebarMenuBadge>
                    ) : null}
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarSeparator />

        {/* Features group — Tools, MCP servers, Skills */}
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu className="gap-1">
              <AppSidebarCollapsibleNavItem
                icon={Plug2Icon}
                label={t('section.capabilities')}
                tooltip={t('tooltip.capabilities')}
                isActive={isFeatureActive}
                expanded={featuresExpanded}
                isSidebarCollapsed={state === 'collapsed'}
                onExpandSidebar={expandSidebarFromIcon}
                onToggle={() => setFeaturesExpanded(!featuresExpanded)}
                items={featureChildren}
                menuClass={activeMenuClass}
              />
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarUtilityFooter />
      <SidebarRail />
    </Sidebar>
  )
}
