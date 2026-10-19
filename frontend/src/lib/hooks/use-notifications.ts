'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { notificationsApi } from '@/lib/api/notifications'
import { notificationQueryKeys } from '@/lib/query-keys/notifications'

/** S25: the unread badge refreshes every 60 seconds and on window focus. */
export const UNREAD_POLL_MS = 60_000

export function useUnreadNotificationCount(orgId: string | null | undefined) {
  return useQuery({
    queryKey: notificationQueryKeys.unread(orgId),
    queryFn: notificationsApi.unreadCount,
    enabled: Boolean(orgId),
    refetchInterval: UNREAD_POLL_MS,
    refetchOnWindowFocus: true,
  })
}

/** The dropdown list (`limit=20`), fetched only while the menu is open. */
export function useNotifications(orgId: string | null | undefined, open: boolean) {
  return useQuery({
    queryKey: notificationQueryKeys.list(orgId),
    queryFn: () => notificationsApi.list(20),
    enabled: Boolean(orgId) && open,
  })
}

function useRefresh(orgId: string | null | undefined) {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: notificationQueryKeys.org(orgId) })
}

export function useMarkNotificationRead(orgId: string | null | undefined) {
  const refresh = useRefresh(orgId)
  return useMutation({ mutationFn: notificationsApi.markRead, onSuccess: refresh })
}

export function useMarkAllNotificationsRead(orgId: string | null | undefined) {
  const refresh = useRefresh(orgId)
  return useMutation({ mutationFn: notificationsApi.markAllRead, onSuccess: refresh })
}
