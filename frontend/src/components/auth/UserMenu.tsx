'use client'

import { useRouter } from 'next/navigation'
import { ChevronsUpDownIcon, LogOutIcon, SettingsIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SidebarMenuButton } from '@/components/ui/sidebar'
import type { User } from '@/lib/types/user'
import { displayUserName, UserAvatar } from '@/components/auth/UserAvatar'

interface Props {
  user: User
  onLogout: () => void
}

export function UserMenu({ user, onLogout }: Props) {
  const router = useRouter()
  const t = useTranslations('auth.userMenu')
  const label = displayUserName(user)

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="w-full rounded-md ring-sidebar-ring outline-hidden focus-visible:ring-2 [&>svg]:size-4 [&>svg]:shrink-0"
        aria-label={label}
        title={label}
        render={
          <SidebarMenuButton
            size="lg"
            className="border border-transparent hover:border-sidebar-border/70 hover:bg-white/70 data-open:border-[var(--moldy-border-mint)] data-open:bg-white data-open:text-sidebar-accent-foreground dark:hover:bg-white/5 dark:data-open:bg-white/8"
          />
        }
      >
        <UserAvatar user={user} size="sm" />
        <div className="grid flex-1 min-w-0 text-left leading-tight group-data-[collapsible=icon]:hidden">
          <span className="flex items-center gap-1.5 truncate text-sm font-medium">
            <span className="truncate">{label}</span>
            {user.is_super_user ? (
              <span className="inline-flex h-4 items-center rounded-full bg-status-accent/15 px-1.5 moldy-ui-micro font-medium uppercase tracking-wider text-status-accent">
                {t('adminBadge')}
              </span>
            ) : null}
          </span>
          <span className="truncate text-xs text-muted-foreground">{user.email}</span>
        </div>
        <ChevronsUpDownIcon className="ml-auto size-4 group-data-[collapsible=icon]:hidden" />
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-[--anchor-width]">
        <DropdownMenuItem
          onClick={(e) => {
            e.preventDefault()
            router.push('/settings')
          }}
        >
          <SettingsIcon />
          {t('settings')}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={(e) => {
            e.preventDefault()
            onLogout()
          }}
          className="text-destructive focus:text-destructive focus:bg-destructive/10"
        >
          <LogOutIcon />
          {t('logout')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
