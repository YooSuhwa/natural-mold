import { apiFetch } from './client'
import type { NotificationPage, UnreadCount } from '@/lib/types/notification'

const BASE = '/api/notifications'

/** Notification API (plan 14.2, S25; H-02). Rows are the caller's own, current org only. */
export const notificationsApi = {
  list: (limit = 20) =>
    apiFetch<NotificationPage>(`${BASE}?${new URLSearchParams({ limit: String(limit) })}`),
  unreadCount: () => apiFetch<UnreadCount>(`${BASE}/unread-count`),
  markRead: (id: string) =>
    apiFetch<void>(`${BASE}/${encodeURIComponent(id)}/read`, { method: 'POST' }),
  markAllRead: () => apiFetch<{ updated: number }>(`${BASE}/read-all`, { method: 'POST' }),
}
