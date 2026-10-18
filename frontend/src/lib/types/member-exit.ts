/** E-02 member removal contracts (`backend/app/schemas/org_member_exit.py`, plan 10.7, S15). */

/** Types the removal settles in one transaction (`ExitResourceType`). */
export type ExitResourceType = 'agent' | 'skill' | 'tool' | 'mcp_server' | 'credential'
export type ExitAction = 'transfer' | 'delete'

export interface OwnedResource {
  /** An `ExitResourceType`, or `marketplace_item` (no transfer path until I-04). */
  resource_type: string
  resource_id: string
  name: string
  /** Agents in the trash stay with their owner (10.7 "보관"). */
  archived: boolean
  transferable: boolean
  /** `409 transfer_blocked` details (or credential `skill_bindings`), if any. */
  blocked_by: Record<string, unknown> | null
  /** The caller holds `can_govern` (E-01 transfer); legacy admins do not. */
  can_transfer_now?: boolean
}

export interface HeldGrant {
  grant_id: string
  resource_type: string
  resource_id: string
  relation: string
}

export interface MemberExitPreview {
  user_id: string
  status: string
  is_last_admin: boolean
  owned: OwnedResource[]
  grants: HeldGrant[]
  group_ids: string[]
  roles: string[]
  trigger_ids: string[]
  deployment_ids: string[]
  api_key_ids: string[]
}

export interface ExitResolution {
  resource_type: ExitResourceType
  resource_id: string
  action: ExitAction
  new_owner_id?: string | null
}

export interface MemberRemoval {
  reason: string
  resolutions: ExitResolution[]
}

export interface MemberRemovalResult {
  user_id: string
  status: 'removed'
  transferred: string[]
  deleted: string[]
  revoked_grant_ids: string[]
}
