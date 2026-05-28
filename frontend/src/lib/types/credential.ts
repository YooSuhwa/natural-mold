// Credential domain types — mirrors backend `app/schemas/credential.py`.
// Field, definition, instance, audit log, OAuth2 helpers.

export type FieldKind =
  | 'string'
  | 'password'
  | 'number'
  | 'select'
  | 'multiline'
  | 'json'
  | 'oauth_button'
  | 'toggle'
  | 'collection'

export interface FieldOption {
  name?: string
  value: string | number | boolean
  description?: string
}

export interface FieldTypeOptions {
  password?: boolean
  multiline?: boolean
  rows?: number
  expirable?: boolean
  min?: number
  max?: number
  step?: number
  regex?: string
  min_length?: number
  max_length?: number
}

export interface FieldDisplayOptions {
  // condition: parent field name -> array of accepted values
  show?: Record<string, Array<string | number | boolean>>
  hide?: Record<string, Array<string | number | boolean>>
}

export interface FieldDef {
  name: string
  display_name: string
  kind: FieldKind
  default?: unknown
  required?: boolean
  description?: string | null
  options?: FieldOption[]
  placeholder?: string | null
  type_options?: FieldTypeOptions
  display_options?: FieldDisplayOptions
  // Tool parameters only: filled in by the agent at call time, hidden in the
  // tool-creation form. Backend exposes the field on the LLM args schema.
  runtime_only?: boolean
}

export interface CredentialDefinition {
  key: string
  display_name: string
  icon_id?: string | null
  documentation_url?: string | null
  category: string
  extends: string[]
  properties: FieldDef[]
  has_test: boolean
  has_oauth: boolean
}

export type CredentialStatus = 'active' | 'auth_needed' | 'expired' | 'disabled' | 'unknown'

export interface Credential {
  id: string
  user_id: string
  definition_key: string
  name: string
  field_keys: string[]
  is_shared: boolean
  // True for operator-managed system credentials (Fix Agent / builder /
  // image generation). User-facing pickers MUST hide these.
  is_system?: boolean
  status: CredentialStatus | string
  key_id: string
  last_used_at: string | null
  last_tested_at: string | null
  last_test_result: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface CredentialCreateRequest {
  definition_key: string
  name: string
  data: Record<string, unknown>
  is_shared?: boolean
}

export interface CredentialUpdateRequest {
  name?: string
  data?: Record<string, unknown>
  is_shared?: boolean
  status?: 'active' | 'disabled' | 'expired'
}

export interface CredentialTestResult {
  success: boolean
  http_status?: number | null
  message: string
  details?: Record<string, unknown>
}

export interface CredentialAuditLog {
  id: string
  credential_id: string
  actor_user_id: string | null
  action: string
  source: string
  ip: string | null
  user_agent: string | null
  error: string | null
  log_metadata: Record<string, unknown> | null
  created_at: string
}

export interface OAuth2AuthStartResponse {
  authorization_url: string
  state: string
}

export interface PreviewTestRequest {
  definition_key: string
  data: Record<string, unknown>
}
