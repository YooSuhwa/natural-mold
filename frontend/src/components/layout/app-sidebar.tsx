'use client'

import { useCallback } from 'react'
import { useAtom } from 'jotai'
import {
  BookOpenIcon,
  LayoutTemplateIcon,
  Plug2Icon,
  PlusIcon,
  ServerIcon,
  StoreIcon,
  ToggleLeftIcon,
  ToggleRightIcon,
  WrenchIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import Image from 'next/image'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

import {
  AppSidebarCollapsibleNavItem,
  type AppSidebarNavChild,
} from '@/components/layout/app-sidebar-collapsible-nav-item'
import { AppSidebarFooter } from '@/components/layout/app-sidebar-footer'
import { ChatNavigator } from '@/components/layout/chat-navigator'
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
  useSidebar,
} from '@/components/ui/sidebar'
import { featuresExpandedAtom } from '@/lib/stores/sidebar-store'

export function AppSidebar() {
  const pathname = usePathname()
  const { setOpen, state, toggleSidebar } = useSidebar()
  const t = useTranslations('sidebar')

  const [featuresExpanded, setFeaturesExpanded] = useAtom(featuresExpandedAtom)

  const buildItems = [
    { label: t('nav.templates'), href: '/agents/new/template', icon: LayoutTemplateIcon },
    { label: t('nav.marketplace'), href: '/marketplace', icon: StoreIcon },
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
            className="moldy-sidebar-control rounded-lg border border-transparent p-1.5 text-primary-strong hover:border-sidebar-border"
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
            className="group/toggle relative flex size-8 items-center justify-center rounded-lg transition-colors hover:bg-sidebar-accent"
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

      <SidebarContent className="min-h-0 px-1">
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

        {/* Build group — Templates, Marketplace */}
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

      <AppSidebarFooter />
      <SidebarRail />
    </Sidebar>
  )
}
