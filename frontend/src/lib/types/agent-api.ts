export type AgentApiScope = 'invoke' | 'stream' | 'background' | 'read'
export type AgentDeploymentIneligibleReasonCode = 'fixed_identity_required'

export interface AgentDeployment {
  id: string
  agent_id: string
  agent_name: string
  public_id: string
  status: 'active' | 'disabled'
  allow_streaming: boolean
  allow_background: boolean
  rate_limit_per_minute: number | null
  daily_token_limit: number | null
  created_at: string
  updated_at: string
}

export interface AgentDeploymentCandidate {
  agent_id: string
  agent_name: string
  runtime_name: string | null
  existing_deployment_id: string | null
  existing_public_id: string | null
  eligible: boolean
  ineligible_reason: string | null
  ineligible_reason_code: AgentDeploymentIneligibleReasonCode | null
}

export interface AgentApiKeyDeploymentRef {
  deployment_id: string
  agent_id: string
  agent_name: string
  public_id: string
  status: 'active' | 'disabled'
}

export interface AgentApiKey {
  id: string
  name: string
  description: string | null
  key_id: string
  prefix: string
  last_four: string
  scopes: AgentApiScope[]
  allow_all_deployments: boolean
  deployments: AgentApiKeyDeploymentRef[]
  revoked_at: string | null
  expires_at: string | null
  last_used_at: string | null
  usage_count: number
  created_at: string
}

export interface AgentApiKeyCreateRequest {
  name: string
  description?: string | null
  scopes: AgentApiScope[]
  allow_all_deployments: boolean
  deployment_ids: string[]
  expires_in_days?: number | null
}

export interface AgentApiKeyCreated extends AgentApiKey {
  key: string
}

export interface AgentDeploymentCreateRequest {
  agent_id: string
  allow_streaming?: boolean
  allow_background?: boolean
  rate_limit_per_minute?: number | null
  daily_token_limit?: number | null
}
