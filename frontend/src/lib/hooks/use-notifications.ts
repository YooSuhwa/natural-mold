'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { downloadApiFile } from '@/lib/hooks/use-api-object-url'
import {
  currentOrgScope,
  isOrgScopeCurrent,
  signalForOrgScope,
} from '@/lib/org-scope/org-scope-signal'
import type { WorkNotificationKind } from '@/lib/types/notification'
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

export function useOpenWorkNotification() {
  return useMutation({
    mutationFn: async ({
      id,
      kind,
      fallbackFilename,
    }: {
      id: string
      kind: WorkNotificationKind
      fallbackFilename: string
    }) => {
      const scope = currentOrgScope()
      try {
        const destination = await notificationsApi.destination(id)
        if (!isOrgScopeCurrent(scope)) return null
        if (destination.artifact_id || destination.version_id) {
          if (!destination.artifact_id || !destination.version_id)
            throw new Error('Incomplete file reference')
          const resultPath = notificationsApi.resultPath(id)
          await downloadApiFile(
            resultPath,
            destination.filename ?? fallbackFilename,
            signalForOrgScope(scope, resultPath),
          )
        }
        if (!isOrgScopeCurrent(scope)) return null
        const path = `/agents/${encodeURIComponent(destination.agent_id)}/conversations/${encodeURIComponent(destination.conversation_id)}`
        return kind === 'chat_task.completed' ? path : `${path}#conversation-goal`
      } catch (error) {
        if (!isOrgScopeCurrent(scope)) return null
        throw error
      }
    },
  })
}
