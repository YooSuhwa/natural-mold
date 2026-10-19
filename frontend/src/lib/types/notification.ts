/** Notification contracts (plan 14.2, S25; backend `app/schemas/notification.py`). */

export type NotificationKind =
  | 'access_request.created'
  | 'access_request.decided'
  | 'grant.received'
  | 'trigger.blocked'
  | 'connection.owner_needed'
  | 'delegation.broken'
  | 'ownership.transferred'
  | 'org.invitation'
  | 'tenant.limit_warning'
  | 'tenant.suspended'
  | 'marketplace.publish_requested'
  | 'marketplace.publish_decided'
  | 'usage.limit_warning'
  | 'agent_memo.proposal'

/** `accessible: false` — the recipient lost access; the server withholds id and name. */
export interface NotificationTarget {
  type: string
  id: string | null
  name: string | null
  accessible: boolean
}

export interface NotificationActor {
  id: string
  display_name: string | null
}

export interface NotificationView {
  id: string
  /** A kind this client does not know yet falls back to a generic line. */
  kind: string
  org_id: string | null
  target: NotificationTarget | null
  actor: NotificationActor | null
  payload: Record<string, string | number | boolean | null>
  link: string | null
  read_at: string | null
  created_at: string
}

export interface NotificationPage {
  items: NotificationView[]
  next_cursor: string | null
  has_more: boolean
}

export interface UnreadCount {
  total: number
  by_org: Record<string, number>
}
