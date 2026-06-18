'use client'

import type { LucideIcon } from 'lucide-react'
import Link from 'next/link'

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from '@/components/ui/sidebar'

const MAX_BADGE_COUNT = 99

export type SidebarNavItemConfig = {
  readonly href: string
  readonly label: string
  readonly icon: LucideIcon
  readonly exact?: boolean
  readonly badge?: number
}

export type SidebarNavSectionConfig = {
  readonly label: string
  readonly items: readonly SidebarNavItemConfig[]
}

type SidebarNavSectionProps = {
  readonly section: SidebarNavSectionConfig
  readonly pathname: string
  readonly menuClassName: string
  readonly onNavigate?: () => void
}

function isActivePath(pathname: string, item: SidebarNavItemConfig): boolean {
  if (item.exact) return pathname === item.href
  return pathname === item.href || pathname.startsWith(`${item.href}/`)
}

function formatBadge(count: number): string {
  return count > MAX_BADGE_COUNT ? `${MAX_BADGE_COUNT}+` : String(count)
}

export function SidebarNavSection({
  section,
  pathname,
  menuClassName,
  onNavigate,
}: SidebarNavSectionProps) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>{section.label}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu className="gap-1">
          {section.items.map((item) => {
            const active = isActivePath(pathname, item)
            return (
              <SidebarMenuItem key={item.href}>
                <SidebarMenuButton
                  isActive={active}
                  tooltip={item.label}
                  render={
                    <Link
                      href={item.href}
                      aria-current={active ? 'page' : undefined}
                      onClick={onNavigate}
                    />
                  }
                  className={menuClassName}
                >
                  <item.icon className="size-4" />
                  <span>{item.label}</span>
                </SidebarMenuButton>
                {item.badge ? <SidebarMenuBadge>{formatBadge(item.badge)}</SidebarMenuBadge> : null}
              </SidebarMenuItem>
            )
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
