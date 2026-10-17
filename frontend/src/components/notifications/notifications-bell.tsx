'use client'

import { BellIcon, CheckCheckIcon } from 'lucide-react'
import { useFormatter, useTranslations } from 'next-intl'
import { useRouter } from 'next/navigation'
import { useState } from 'react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useActiveOrganization } from '@/lib/hooks/use-organizations'
import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotifications,
  useUnreadNotificationCount,
} from '@/lib/hooks/use-notifications'
import type { NotificationView } from '@/lib/types/notification'
import { cn } from '@/lib/utils'
import { notificationText } from './notification-text'

function badgeLabel(count: number): string {
  return count > 99 ? '99+' : String(count)
}

/**
 * S25 bell in the sidebar utility row: unread badge (99+ cap) polled every 60 s, the
 * newest 20 notifications of the current organization when opened. Clicking a row marks
 * it read and follows its link (none when the target is no longer accessible).
 */
export function NotificationsBell() {
  const t = useTranslations('notifications')
  const format = useFormatter()
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const orgId = useActiveOrganization().activeOrganization?.id
  const unread = useUnreadNotificationCount(orgId).data?.total ?? 0
  const list = useNotifications(orgId, open)
  const markRead = useMarkNotificationRead(orgId)
  const markAll = useMarkAllNotificationsRead(orgId)
  const label = unread > 0 ? t('bellUnread', { count: unread }) : t('bell')
  const items = list.data?.items ?? []

  function select(item: NotificationView) {
    setOpen(false)
    if (!item.read_at) markRead.mutate(item.id)
    if (item.link) router.push(item.link)
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        render={<button type="button" aria-label={label} />}
        className="moldy-sidebar-control relative inline-flex h-7 items-center justify-center rounded-lg px-1.5 data-open:bg-sidebar-accent"
        aria-label={label}
        title={label}
        data-testid="notifications-bell"
      >
        <BellIcon className="size-4" aria-hidden />
        {unread > 0 ? (
          <span
            aria-hidden
            className="absolute -top-1 -right-1 min-w-4 rounded-full bg-destructive px-1 text-[10px] leading-4 font-semibold text-white"
          >
            {badgeLabel(unread)}
          </span>
        ) : null}
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-80">
        <div className="flex items-center justify-between px-2 py-1.5">
          <span className="text-sm font-semibold">{t('title')}</span>
        </div>
        {unread > 0 ? (
          <DropdownMenuItem onClick={() => markAll.mutate()} disabled={markAll.isPending}>
            <CheckCheckIcon className="size-4" aria-hidden />
            {t('markAllRead')}
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator />
        {list.isError ? (
          <p role="alert" className="px-2 py-3 text-sm text-muted-foreground">
            {t('loadFailed')}
          </p>
        ) : list.isPending ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">{t('loading')}</p>
        ) : items.length === 0 ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">{t('empty')}</p>
        ) : (
          items.map((item) => (
            <DropdownMenuItem
              key={item.id}
              onClick={() => select(item)}
              className="flex flex-col items-start gap-0.5"
              data-unread={item.read_at ? undefined : 'true'}
            >
              <span className={cn('text-sm', !item.read_at && 'font-semibold')}>
                {!item.read_at ? <span className="sr-only">{t('unreadMark')} </span> : null}
                {notificationText(item, t)}
              </span>
              <span className="text-xs text-muted-foreground">
                {format.relativeTime(new Date(item.created_at))}
              </span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
