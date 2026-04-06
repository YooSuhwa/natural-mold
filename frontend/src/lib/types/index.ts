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

// Tool
export interface Tool {
  id: string
  type: 'mcp' | 'custom' | 'builtin' | 'prebuilt'
  is_system: boolean
  mcp_server_id: string | null
  name: string
  description: string | null
  parameters_schema: Record<string, unknown> | null
  api_url: string | null
  http_method: string | null
  auth_type: string | null
  auth_config: Record<string, unknown> | null
  tags: string[] | null
  server_key_available: boolean
  agent_count: number
  created_at: string
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

export interface ToolCustomCreateRequest {
  name: string
  description?: string
  api_url: string
  http_method?: string
  parameters_schema?: Record<string, unknown>
  auth_type?: string
  auth_config?: Record<string, unknown>
}

export interface MCPServerCreateRequest {
  name: string
  url: string
  auth_type?: string
  auth_config?: Record<string, unknown>
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

export type SSEEvent =
  | { event: 'message_start'; data: { id: string; role: string } }
  | { event: 'content_delta'; data: { delta?: string; content?: string } }
  | { event: 'tool_call_start'; data: { name: string; args: Record<string, unknown> } }
  | { event: 'tool_call_result'; data: { result: string } }
  | { event: 'message_end'; data: { content: string; usage: Record<string, number> } }
  | { event: 'error'; data: { message: string } }

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

// Creation Session
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
