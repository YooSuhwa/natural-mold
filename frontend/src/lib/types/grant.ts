/**
 * Resource sharing types — mirror backend `app/schemas/grant.py` (plan 9.7, 10.1-10.2, 10.7).
 */

export type ShareResourceType = 'agent' | 'skill' | 'tool' | 'mcp_server' | 'credential'
export type GrantPrincipalType = 'user' | 'group' | 'organization'
export type ShareLevel = 'consumer' | 'viewer' | 'editor' | 'manager'
export type AccessLevel = ShareLevel | 'owner' | 'governor'
export type AccessSource = 'owner' | 'direct' | 'group' | 'organization' | 'governance'
export type SharePermission = 'can_share' | 'can_revoke' | 'can_govern'

export interface PrincipalRef {
  type: GrantPrincipalType
  id: string
}

export interface PrincipalView extends PrincipalRef {
  display_name: string | null
  member_count: number | null
}

export interface GrantCreate {
  principal: PrincipalRef
  level: ShareLevel
  /** Agents with `consumer` only (10.2). */
  expires_at?: string | null
  /** D17: the caller saw the delegated account resources (`confirm_required`). */
  confirm_delegation?: boolean
}

export interface GrantView {
  grant_id: string
  principal: PrincipalRef
  level: ShareLevel
  expires_at: string | null
  granted_by: string | null
  granted_at: string
}

export interface GrantList {
  items: GrantView[]
}

export interface AccessEntry {
  grant_id: string | null
  principal: PrincipalView
  level: AccessLevel
  source: AccessSource
  revocable: boolean
  expires_at: string | null
}

export interface AccessList {
  items: AccessEntry[]
  my_permissions: SharePermission[]
}

export interface OwnershipTransfer {
  new_owner_id: string
  reason?: string | null
  /** Leave the previous owner a manager grant (S17). */
  keep_manager?: boolean
}

export interface OwnershipTransferResult {
  resource_type: ShareResourceType
  resource_id: string
  owner_id: string
  previous_owner_id: string | null
  manager_grant_id: string | null
}

/** `confirm_required` `details.delegations[]` (D17). */
export interface DelegatedAccount {
  resource_type: string
  resource_id: string
  name: string
}
