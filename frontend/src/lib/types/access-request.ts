/**
 * Access request types — mirror backend `app/schemas/access_request.py` (plan 9.7, 10.9, S18).
 */

export type RequestableType = 'agent' | 'skill' | 'tool' | 'mcp_server'
export type RequestLevel = 'consumer' | 'viewer' | 'editor'
export type RequestStatus = 'pending' | 'approved' | 'denied' | 'cancelled' | 'expired'
export type RequestBox = 'inbox' | 'outbox' | 'handled'

export interface AccessRequestCreate {
  resource_type: RequestableType
  resource_id: string
  level: RequestLevel
  reason?: string | null
}

export interface AccessRequestApprove {
  /** Lower than requested only; omitted → as requested. */
  level?: RequestLevel | null
  expires_at?: string | null
  confirm_delegation?: boolean
  note?: string | null
}

export interface AccessRequestView {
  id: string
  resource_type: RequestableType
  resource_id: string
  resource_name: string | null
  level: RequestLevel
  reason: string | null
  status: RequestStatus
  requester: { id: string; display_name: string | null }
  decided_by: string | null
  decided_at: string | null
  decision_note: string | null
  granted_level: RequestLevel | null
  created_at: string
  expires_at: string
}

export interface AccessRequestPage {
  items: AccessRequestView[]
  next_cursor: string | null
  has_more: boolean
}
