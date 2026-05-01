import { apiFetch } from './client'
import type { ShareLink, SharedConversationView } from '@/lib/types/share'

export const sharesApi = {
  /** Owner: fetch the active share link, or ``null`` when private. */
  getActive: (conversationId: string) =>
    apiFetch<ShareLink | null>(`/api/conversations/${conversationId}/share`),

  /** Owner: publish the conversation. Idempotent — same token if already shared. */
  create: (conversationId: string) =>
    apiFetch<ShareLink>(`/api/conversations/${conversationId}/share`, {
      method: 'POST',
    }),

  /** Owner: revoke the active share. Idempotent (no-op when already private). */
  revoke: (conversationId: string) =>
    apiFetch<void>(`/api/conversations/${conversationId}/share`, {
      method: 'DELETE',
    }),

  /** Public: read-only conversation snapshot for visitors. */
  getPublic: (shareToken: string) =>
    apiFetch<SharedConversationView>(`/api/shares/${shareToken}`),
}
