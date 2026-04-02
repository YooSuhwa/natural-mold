// Agent
export interface Agent {
  id: string
  name: string
  description: string | null
  system_prompt: string
  model: ModelBrief
  tools: ToolBrief[]
  status: string
  template_id: string | null
  created_at: string
  updated_at: string
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
  template_id?: string
}

export interface AgentUpdateRequest {
  name?: string
  description?: string
  system_prompt?: string
  model_id?: string
  tool_ids?: string[]
}

// Model
export interface Model {
  id: string
  provider: string
  model_name: string
  display_name: string
  base_url: string | null
  is_default: boolean
  cost_per_input_token: number | null
  cost_per_output_token: number | null
  created_at: string
}

export interface ModelCreateRequest {
  provider: string
  model_name: string
  display_name: string
  base_url?: string
  api_key?: string
  is_default?: boolean
  cost_per_input_token?: number
  cost_per_output_token?: number
}

// Tool
export interface Tool {
  id: string
  type: "mcp" | "custom" | "builtin"
  is_system: boolean
  mcp_server_id: string | null
  name: string
  description: string | null
  parameters_schema: Record<string, unknown> | null
  api_url: string | null
  http_method: string | null
  auth_type: string | null
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
  created_at: string
  updated_at: string
}

export interface Message {
  id: string
  conversation_id: string
  role: "user" | "assistant" | "tool"
  content: string
  tool_calls: ToolCallInfo[] | null
  tool_call_id: string | null
  created_at: string
}

export interface ToolCallInfo {
  name: string
  args: Record<string, unknown>
}

// SSE Events
export type SSEEventType =
  | "message_start"
  | "content_delta"
  | "tool_call_start"
  | "tool_call_result"
  | "message_end"
  | "error"

export interface SSEEvent {
  event: SSEEventType
  data: Record<string, unknown>
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

// Trigger
export interface AgentTrigger {
  id: string
  agent_id: string
  trigger_type: "interval" | "cron"
  schedule_config: { interval_minutes?: number; cron_expression?: string }
  input_message: string
  status: "active" | "paused" | "error"
  last_run_at: string | null
  next_run_at: string | null
  run_count: number
  created_at: string
  updated_at: string
}

export interface TriggerCreateRequest {
  trigger_type: "interval" | "cron"
  schedule_config: Record<string, unknown>
  input_message: string
}

export interface TriggerUpdateRequest {
  trigger_type?: string
  schedule_config?: Record<string, unknown>
  input_message?: string
  status?: string
}
