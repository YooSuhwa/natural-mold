'use client'

import { ChevronRightIcon, type LucideIcon } from 'lucide-react'
import Link from 'next/link'
import {
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
} from '@/components/ui/sidebar'

export type AppSidebarNavChild = {
  label: string
  href: string
  icon: LucideIcon
  isActive: boolean
}

interface CollapsibleNavItemProps {
  icon: LucideIcon
  label: string
  tooltip: string
  isActive: boolean
  expanded: boolean
  onToggle: () => void
  items: readonly AppSidebarNavChild[]
  menuClass: string
}

export function AppSidebarCollapsibleNavItem({
  icon: Icon,
  label,
  tooltip,
  isActive,
  expanded,
  onToggle,
  items,
  menuClass,
}: CollapsibleNavItemProps) {
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        isActive={isActive}
        tooltip={tooltip}
        onClick={onToggle}
        aria-expanded={expanded}
        className={menuClass}
      >
        <Icon className="size-4" />
        <span>{label}</span>
        <ChevronRightIcon
          className={`ml-auto size-4 transition-transform ${expanded ? 'rotate-90' : ''}`}
        />
      </SidebarMenuButton>
      {expanded ? (
        <SidebarMenuSub>
          {items.map((child) => (
            <SidebarMenuSubItem key={child.href}>
              <SidebarMenuSubButton
                isActive={child.isActive}
                render={<Link href={child.href} />}
                className={menuClass}
              >
                <child.icon className="size-4" />
                <span>{child.label}</span>
              </SidebarMenuSubButton>
            </SidebarMenuSubItem>
          ))}
        </SidebarMenuSub>
      ) : null}
    </SidebarMenuItem>
  )
}
