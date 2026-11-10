// Tool domain types — mirrors backend `app/schemas/tool.py`.

import type { RegistrationState } from './approval'
import type { FieldDef } from './credential'
import type { CredentialMode } from './readiness'
import type { ToolApprovalToolV1 } from './runtime-policy'

export interface ToolDefinition {
  key: string
  display_name: string
  description: string
  icon_id?: string | null
  category: string
  parameters: FieldDef[]
  credential_definition_keys: string[]
  requires_credential: boolean
}

export interface ToolInstance {
  id: string
  user_id: string | null
  definition_key: string
  name: string
  description: string | null
  parameters: Record<string, unknown>
  credential_id: string | null
  credential_mode?: CredentialMode
  credential_definition_key?: string | null
  enabled: boolean
  /** The tool's own approval default (an agent starts from it). */
  approval_default?: ToolApprovalToolV1 | null
  /** Anything but ``approved`` is neither attached to agents nor loaded into runs. */
  registration_state?: RegistrationState
  last_used_at: string | null
  created_at: string
  updated_at: string
}

export interface ToolCreateRequest {
  definition_key: string
  name: string
  description?: string | null
  parameters?: Record<string, unknown>
  credential_id?: string | null
  enabled?: boolean
}

export interface ToolPatchRequest {
  name?: string
  description?: string | null
  parameters?: Record<string, unknown>
  credential_id?: string | null
  enabled?: boolean
}

export interface ToolRunResult {
  success: boolean
  result?: unknown
  error?: string | null
  http_status?: number | null
  duration_ms: number
}
