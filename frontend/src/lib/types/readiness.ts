/**
 * Readiness check types — mirror backend `app/schemas/readiness.py` (plan 12.1–12.3, S21).
 */

export type ReadinessResult =
  | 'ready'
  | 'no_permission'
  | 'connect_by_me'
  | 'owner_must_connect'
  | 'unavailable'
export type ReadinessOverall = 'ready' | 'needs_connection' | 'limited' | 'blocked'
export type ReadinessKind = 'llm_model' | 'tool' | 'mcp_server' | 'skill' | 'sub_agent'

export interface ReadinessCredential {
  mode: string
  /** active | auth_needed | expired | missing | unusable */
  status: string
  /** Only the caller's own credential. */
  credential_id?: string
  /** `connect_by_me` only: the credential type the caller connects (S23). */
  definition_key?: string
}

export interface ReadinessAction {
  type: 'request_access' | 'notify_owner' | 'connect' | 'oauth_start'
  via?: string
}

/** `id`/`name` are absent on items the caller may not see; `count` is set instead. */
export interface ReadinessItem {
  kind: ReadinessKind
  required: boolean
  result: ReadinessResult
  id?: string | null
  agent_id?: string
  name?: string | null
  count?: number
  credential?: ReadinessCredential
  action?: ReadinessAction | null
}

export interface AgentReadiness {
  /** Only for a caller who may view the agent's settings. */
  agent_id?: string
  overall: ReadinessOverall
  identity_mode?: string
  items: ReadinessItem[]
}

export interface ReadinessSummary {
  overall: ReadinessOverall
  counts: Partial<Record<ReadinessResult, number>>
}

export type BindableResourceType = 'tool' | 'mcp_server'

/** How a tool or MCP server gets its account (plan D15): none, the resource's own, or each caller's. */
export type CredentialMode = 'none' | 'shared' | 'per_user'

/** `PUT /api/{tools|mcp-servers}/{id}/credential-mode` response. */
export interface CredentialModeResult {
  resource_type: BindableResourceType
  resource_id: string
  credential_mode: CredentialMode
  credential_definition_key: string | null
  binding_count: number
}

export interface CredentialBinding {
  id: string
  resource_type: BindableResourceType
  resource_id: string
  credential_id: string
}
