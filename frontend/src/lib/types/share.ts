import type { Message } from './index'

/**
 * Owner-facing share link metadata. ``revoked_at`` is non-null only for
 * historical rows surfaced through audit endpoints — the active-link
 * fetch always returns either ``null`` (no active share) or a row with
 * ``revoked_at: null``.
 */
export interface ShareLink {
  id: string
  share_token: string
  conversation_id: string
  created_at: string
  revoked_at: string | null
}

export interface SharedAgentBrief {
  name: string
  description: string | null
  image_url: string | null
}

/**
 * Public read-only conversation snapshot returned by ``/api/shares/{token}``.
 * Visitors render this without authentication.
 */
export interface SharedConversationView {
  share_token: string
  conversation_title: string | null
  conversation_created_at: string
  agent: SharedAgentBrief
  messages: Message[]
  shared_at: string
}
