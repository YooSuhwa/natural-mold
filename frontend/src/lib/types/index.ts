// Agent
export interface Agent {
  id: string
  name: string
  description: string | null
  system_prompt: string
  model: ModelBrief
  tools: ToolBrief[]
  skills: SkillBrief[]
  status: string
  is_favorite: boolean
  model_params: ModelParams | null
  middleware_configs: MiddlewareConfigEntry[]
  template_id: string | null
  created_at: string
  updated_at: string
  image_url: string | null
}

export interface ModelParams {
  temperature?: number
  top_p?: number
  max_tokens?: number
}

export interface ModelBrief {
  id: string
  display_name: string
}

export interface ToolBrief {
  id: string
  name: string
}

export interface AgentCreateRequest {
  name: string
  description?: string
  system_prompt: string
  model_id: string
  tool_ids?: string[]
  skill_ids?: string[]
  template_id?: string
  model_params?: ModelParams
  middleware_configs?: MiddlewareConfigEntry[]
}

export interface AgentUpdateRequest {
  name?: string
  description?: string
  system_prompt?: string
  model_id?: string
  tool_ids?: string[]
  skill_ids?: string[]
  is_favorite?: boolean
  model_params?: ModelParams
  middleware_configs?: MiddlewareConfigEntry[]
}

// Provider
export type ProviderType = 'openai' | 'anthropic' | 'google' | 'openrouter' | 'openai_compatible'

export interface Provider {
  id: string
  name: string
  provider_type: ProviderType
  base_url: string | null
  is_active: boolean
  has_api_key: boolean
  model_count: number
  created_at: string
  updated_at: string
}

export interface ProviderCreateRequest {
  name: string
  provider_type: ProviderType
  base_url?: string
  api_key?: string
}

export interface ProviderUpdateRequest {
  name?: string
  base_url?: string
  api_key?: string
}

export interface ProviderTestResponse {
  success: boolean
  message: string
  models_count: number | null
}

export interface DiscoveredModel {
  model_name: string
  display_name: string
  context_window: number | null
  max_output_tokens: number | null
  input_modalities: string[] | null
  output_modalities: string[] | null
  cost_per_input_token: number | null
  cost_per_output_token: number | null
  supports_vision: boolean | null
  supports_function_calling: boolean | null
  supports_reasoning: boolean | null
}

// Model
export interface Model {
  id: string
  provider_id: string
  provider: string
  provider_name: string
  model_name: string
  display_name: string
  base_url: string | null
  is_default: boolean
  context_window: number | null
  max_output_tokens: number | null
  input_modalities: string[] | null
  output_modalities: string[] | null
  cost_per_input_token: number | null
  cost_per_output_token: number | null
  supports_vision: boolean | null
  supports_function_calling: boolean | null
  supports_reasoning: boolean | null
  agent_count: number
  created_at: string
}

export interface ModelCreateRequest {
  provider_id: string
  provider?: string
  model_name: string
  display_name: string
  base_url?: string
  api_key?: string
  is_default?: boolean
  cost_per_input_token?: number
  cost_per_output_token?: number
}

export interface ModelUpdateRequest {
  provider?: string
  model_name?: string
  display_name?: string
  base_url?: string
  api_key?: string
  is_default?: boolean
  cost_per_input_token?: number
  cost_per_output_token?: number
}

export interface ModelBulkCreateRequest {
  provider_id: string
  models: {
    model_name: string
    display_name: string
    context_window?: number | null
    max_output_tokens?: number | null
    input_modalities?: string[] | null
    output_modalities?: string[] | null
    cost_per_input_token?: number | null
    cost_per_output_token?: number | null
    supports_vision?: boolean | null
    supports_function_calling?: boolean | null
    supports_reasoning?: boolean | null
  }[]
}

// Credential
export interface Credential {
  id: string
  name: string
  credential_type: 'api_key' | 'oauth2'
  provider_name: string
  is_active: boolean
  has_data: boolean
  field_keys: string[]
  created_at: string
  updated_at: string
}

export interface CredentialProviderDef {
  key: string
  name: string
  credential_type: string
  fields: CredentialFieldDef[]
}

export interface CredentialFieldDef {
  key: string
  label: string
  secret: boolean
  default?: string
}

export interface CredentialCreateRequest {
  name: string
  credential_type: string
  provider_name: string
  data: Record<string, string>
}

export interface CredentialUpdateRequest {
  name?: string
  data?: Record<string, string>
}

export interface CredentialUsage {
  tool_count: number
}

// Tool
export interface Tool {
  id: string
  type: 'mcp' | 'custom' | 'builtin' | 'prebuilt'
  is_system: boolean
  // M6.1 백엔드 drop 완료 — 응답에 더 이상 포함되지 않음. M5에서 frontend MCP
  // re-wire 시 함께 제거 예정 (현재는 useToolsByMCPServer 등 dead path가 참조).
  mcp_server_id?: string | null
  // PREBUILT tool의 provider 식별자. connection 조회에 사용. 그 외 타입은 null.
  provider_name: string | null
  name: string
  description: string | null
  parameters_schema: Record<string, unknown> | null
  api_url: string | null
  http_method: string | null
  auth_type: string | null
  tags: string[] | null
  connection_id: string | null
  agent_count: number
  created_at: string
}

// Connection — ADR-008 (user × type × provider 수준 credential 바인딩)
export type ConnectionType = 'prebuilt' | 'mcp' | 'custom'

// PREBUILT connection에서 허용되는 provider_name 집합. backend
// `credential_registry.CREDENTIAL_PROVIDERS` 의 enum 키와 일치 — 추가 시 양측 동기.
export type PrebuiltProviderName =
  | 'naver'
  | 'google_search'
  | 'google_chat'
  | 'google_workspace'

export const PREBUILT_PROVIDER_NAMES: readonly PrebuiltProviderName[] = [
  'naver',
  'google_search',
  'google_chat',
  'google_workspace',
]

// `tool.authDialog.provider.*` 메시지 키 매핑. PrebuiltProviderName 추가 시 여기도 동기.
export const PREBUILT_PROVIDER_I18N_KEY: Record<PrebuiltProviderName, string> = {
  naver: 'naver',
  google_search: 'googleSearch',
  google_chat: 'googleChat',
  google_workspace: 'googleWorkspace',
}

// CUSTOM connection의 provider_name sentinel. backend `credential_registry`와 동기.
export const CUSTOM_CONNECTION_PROVIDER_NAME = 'custom_api_key'

export function isPrebuiltProviderName(
  value: string | null | undefined,
): value is PrebuiltProviderName {
  return (
    typeof value === 'string' &&
    (PREBUILT_PROVIDER_NAMES as readonly string[]).includes(value)
  )
}
export type ConnectionStatus = 'active' | 'disabled'
export type ConnectionMcpAuthType = 'none' | 'bearer' | 'api_key' | 'oauth2' | 'basic'
export type ConnectionMcpTransport = 'http' | 'stdio'

export interface ConnectionExtraConfigResponse {
  url: string
  auth_type: ConnectionMcpAuthType
  header_keys: string[] | null
  env_var_keys: string[] | null
  transport: ConnectionMcpTransport | null
  timeout: number | null
}

export interface Connection {
  id: string
  user_id: string
  type: ConnectionType
  provider_name: string
  display_name: string
  credential_id: string | null
  extra_config: ConnectionExtraConfigResponse | null
  is_default: boolean
  status: ConnectionStatus
  created_at: string
  updated_at: string
}

export interface ConnectionCreateRequest {
  type: ConnectionType
  provider_name: string
  display_name: string
  credential_id?: string | null
  is_default?: boolean
  status?: ConnectionStatus
}

export interface ConnectionUpdateRequest {
  provider_name?: string
  display_name?: string
  credential_id?: string | null
  is_default?: boolean
  status?: ConnectionStatus
}

export interface MCPServer {
  id: string
  name: string
  url: string
  auth_type: string
  status: string
  tools: Tool[]
  created_at: string
}

export interface CredentialBrief {
  id: string
  name: string
  provider_name: string
}

export interface MCPServerListItem {
  id: string
  name: string
  url: string
  auth_type: string
  credential_id: string | null
  credential: CredentialBrief | null
  status: string
  tool_count: number
  created_at: string
}

export interface MCPServerUpdateRequest {
  name?: string
  credential_id?: string | null
  auth_config?: Record<string, unknown>
}

export interface ToolCustomCreateRequest {
  name: string
  description?: string
  api_url: string
  http_method?: string
  parameters_schema?: Record<string, unknown>
  auth_type?: string
  connection_id?: string
}

export interface MCPServerCreateRequest {
  name: string
  url: string
  auth_type?: string
  auth_config?: Record<string, unknown>
  credential_id?: string
}

// Template
export interface Template {
  id: string
  name: string
  description: string | null
  category: string
  system_prompt: string
  recommended_tools: string[] | null
  recommended_model_id: string | null
  usage_example: string | null
  created_at: string
}

// Conversation
export interface Conversation {
  id: string
  agent_id: string
  title: string | null
  is_pinned: boolean
  created_at: string
  updated_at: string
}

export interface ConversationUpdateRequest {
  title?: string
  is_pinned?: boolean
}

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  tool_calls: ToolCallInfo[] | null
  tool_call_id: string | null
  created_at: string
}

export interface ToolCallInfo {
  id?: string
  name: string
  args: Record<string, unknown>
}

// SSE Events
export type SSEEventType =
  | 'message_start'
  | 'content_delta'
  | 'tool_call_start'
  | 'tool_call_result'
  | 'message_end'
  | 'error'
  | 'interrupt'

export type SSEEvent =
  | { event: 'message_start'; data: { id: string; role: string } }
  | { event: 'content_delta'; data: { delta?: string; content?: string } }
  | { event: 'tool_call_start'; data: { tool_name: string; parameters: Record<string, unknown> } }
  | { event: 'tool_call_result'; data: { tool_name: string; result: string } }
  | { event: 'message_end'; data: { content: string; usage: Record<string, number> } }
  | { event: 'error'; data: { message: string } }
  | { event: 'interrupt'; data: InterruptPayload }

// HiTL (Human-in-the-Loop)
export interface InterruptPayload {
  interrupt_id: string
  value: Record<string, unknown>
}

export interface UserInputQuestion {
  question: string
  type: 'single_select' | 'multi_select' | 'text'
  options?: Array<{ label: string; description?: string }>
}

// Usage
export interface UsageSummary {
  period: string
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  estimated_cost_usd: number
  by_agent: AgentUsageRow[]
}

export interface AgentUsageRow {
  agent_id: string
  agent_name: string
  total_tokens: number
  estimated_cost: number
}

// Creation Session (v1 — deprecated, will be removed)
export interface CreationSession {
  id: string
  status: string
  conversation_history: Array<{ role: string; content: string }>
  draft_config: DraftConfig | null
  created_at: string
  updated_at: string
}

export interface DraftConfig {
  name?: string
  description?: string
  system_prompt?: string
  recommended_tool_names?: string[]
  recommended_model?: string
  is_ready?: boolean
}

// Builder v2
export interface BuilderSession {
  id: string
  status: 'building' | 'streaming' | 'preview' | 'confirming' | 'completed' | 'failed'
  current_phase: number
  user_request: string
  intent: BuilderIntent | null
  tools_result: BuilderToolRecommendation[] | null
  middlewares_result: BuilderMiddlewareRecommendation[] | null
  system_prompt: string | null
  draft_config: BuilderDraftConfig | null
  agent_id: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface BuilderIntent {
  agent_name: string
  agent_name_ko: string
  agent_description: string
  primary_task_type: string
  tool_preferences: string
  output_style: string
  response_tone: string
  use_cases: string[]
  constraints: string[]
  required_capabilities: string[]
}

export interface BuilderToolRecommendation {
  tool_name: string
  description: string
  reason: string
}

export interface BuilderMiddlewareRecommendation {
  middleware_name: string
  description: string
  reason: string
}

export interface BuilderDraftConfig {
  name: string
  name_ko: string
  description: string
  system_prompt: string
  tools: string[]
  middlewares: string[]
  model_name: string
  primary_task_type: string
  use_cases: string[]
}

// Builder SSE Events
export type BuilderSSEEventType =
  | 'phase_progress'
  | 'sub_agent_start'
  | 'sub_agent_end'
  | 'build_preview'
  | 'build_failed'
  | 'error'
  | 'info'
  | 'stream_end'

export type BuilderSSEEvent =
  | {
      event: 'phase_progress'
      data: {
        phase: number
        status: 'started' | 'completed' | 'failed' | 'warning'
        message?: string
      }
    }
  | { event: 'sub_agent_start'; data: { phase: number; agent_name: string } }
  | { event: 'sub_agent_end'; data: { phase: number; result_summary: string } }
  | { event: 'build_preview'; data: { draft_config: BuilderDraftConfig } }
  | { event: 'build_failed'; data: { message: string } }
  | { event: 'error'; data: { phase: number; message: string; recoverable: boolean } }
  | { event: 'info'; data: Record<string, unknown> }
  | { event: 'stream_end'; data: Record<string, unknown> }

// Assistant v2
export interface AssistantToolCallResult {
  tool_name: string
  success: boolean
  summary: string
}

// Skill
export interface Skill {
  id: string
  name: string
  description: string | null
  content: string
  type: 'text' | 'package'
  has_scripts: boolean
  created_at: string
  updated_at: string
}

export interface SkillCreateRequest {
  name: string
  description?: string
  content: string
}

export interface SkillUpdateRequest {
  name?: string
  description?: string
  content?: string
}

export interface SkillBrief {
  id: string
  name: string
}

// Middleware
export interface MiddlewareConfigEntry {
  type: string
  params: Record<string, unknown>
}

export interface MiddlewareRegistryItem {
  type: string
  name: string
  display_name: string
  description: string
  category: 'context' | 'planning' | 'safety' | 'reliability' | 'provider'
  config_schema: Record<string, unknown>
  provider_specific: string | null
}

// Trigger
export interface AgentTrigger {
  id: string
  agent_id: string
  trigger_type: 'interval' | 'cron'
  schedule_config: { interval_minutes?: number; cron_expression?: string }
  input_message: string
  status: 'active' | 'paused' | 'error'
  last_run_at: string | null
  next_run_at: string | null
  run_count: number
  created_at: string
  updated_at: string
}

export interface TriggerCreateRequest {
  trigger_type: 'interval' | 'cron'
  schedule_config: Record<string, unknown>
  input_message: string
}

export interface TriggerUpdateRequest {
  trigger_type?: string
  schedule_config?: Record<string, unknown>
  input_message?: string
  status?: string
}
