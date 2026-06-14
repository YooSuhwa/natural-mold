import type { Message } from './index'

/**
 * One SSE event captured during an assistant turn. Mirrors backend
 * ``TraceEvent`` (``backend/app/schemas/conversation.py``).
 */
export interface LegacyTraceEvent {
  id: string | null
  event: string
  data: Record<string, unknown>
}

export interface ProtocolTraceEvent {
  id: string | null
  method: string
  data?: unknown
  namespace?: string[]
  params?: {
    namespace?: string[]
    timestamp?: string | number
    data?: unknown
  }
  seq?: number | null
  event_id?: string | null
  upstream_event_id?: string | null
  run_id?: string | null
  type?: string | null
}

export type TraceEvent = LegacyTraceEvent | ProtocolTraceEvent

/**
 * One assistant turn's full event sequence. Used by W6 (shared page chips)
 * and (later) W3-out resume.
 */
export interface TurnTrace {
  assistant_msg_id: string
  events: TraceEvent[]
  last_event_id: string | null
  /** 이 turn에서 노출된 assistant 메시지의 parsed UUID 목록.
   * MessageResponse.id와 동일 형식이라 직접 매칭 가능 (W6 정확도).
   * null이면 m33 이전 row → chronological 폴백. */
  linked_message_ids: string[] | null
  created_at: string
  completed_at: string | null
}

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
  /** W6 — turn별 SSE event 시퀀스. 도구/Skill 칩 렌더에 사용. W5 머지
   * 이전 대화는 빈 배열로 응답되어 자연스럽게 칩이 안 보인다. */
  traces: TurnTrace[]
  shared_at: string
}
