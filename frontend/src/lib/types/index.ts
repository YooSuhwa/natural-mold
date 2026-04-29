// Re-exports for greenfield domain types. Legacy types (Connection, Provider,
// CredentialFieldDef, etc.) were removed alongside the routers they mirrored.

export * from './credential'
export * from './tool'
export * from './mcp'
export * from './skill'

// ---------- Agent ---------------------------------------------------------

export interface AgentBrief {
  id: string
  name: string
  description?: string | null
  image_url?: string | null
}

export interface ModelBrief {
  id: string
  display_name: string
}

export interface ToolBrief {
  id: string
  name: string
}

export interface SkillBrief {
  id: string
  name: string
  slug?: string
  kind?: 'text' | 'package'
  description?: string | null
}

export interface ModelParams {
  temperature?: number
  top_p?: number
  max_tokens?: number
}

export interface MiddlewareConfigEntry {
  type: string
  params: Record<string, unknown>
}

export interface Agent {
  id: string
  name: string
  description: string | null
  system_prompt: string
  model: ModelBrief
  tools: ToolBrief[]
  skills: SkillBrief[]
  sub_agents: AgentBrief[]
  status: string
  is_favorite: boolean
  model_params: ModelParams | null
  middleware_configs: MiddlewareConfigEntry[]
  template_id: string | null
  created_at: string
  updated_at: string
  image_url: string | null
  opener_questions: string[] | null
  llm_credential_id?: string | null
}

export interface AgentCreateRequest {
  name: string
  description?: string
  system_prompt: string
  model_id: string
  tool_ids?: string[]
  skill_ids?: string[]
  sub_agent_ids?: string[]
  template_id?: string
  model_params?: ModelParams
  middleware_configs?: MiddlewareConfigEntry[]
  opener_questions?: string[]
  llm_credential_id?: string | null
}

export interface AgentUpdateRequest {
  name?: string
  description?: string
  system_prompt?: string
  model_id?: string
  tool_ids?: string[]
  skill_ids?: string[]
  sub_agent_ids?: string[]
  is_favorite?: boolean
  model_params?: ModelParams
  middleware_configs?: MiddlewareConfigEntry[]
  opener_questions?: string[]
  llm_credential_id?: string | null
}

// ---------- Template ------------------------------------------------------

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

// ---------- Conversation / Messages ---------------------------------------

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

export interface ToolCallInfo {
  id?: string
  name: string
  args: Record<string, unknown>
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

// ---------- SSE -----------------------------------------------------------

export type SSEEventType =
  | 'message_start'
  | 'content_delta'
  | 'tool_call_start'
  | 'tool_call_result'
  | 'message_end'
  | 'error'
  | 'interrupt'

export interface InterruptPayload {
  interrupt_id: string
  value: Record<string, unknown>
}

export type SSEEvent =
  | { event: 'message_start'; data: { id: string; role: string } }
  | { event: 'content_delta'; data: { delta?: string; content?: string } }
  | { event: 'tool_call_start'; data: { tool_name: string; parameters: Record<string, unknown> } }
  | { event: 'tool_call_result'; data: { tool_name: string; result: string } }
  | { event: 'message_end'; data: { content: string; usage: Record<string, number> } }
  | { event: 'error'; data: { message: string } }
  | { event: 'interrupt'; data: InterruptPayload }

export interface UserInputQuestion {
  question: string
  type: 'single_select' | 'multi_select' | 'text'
  options?: Array<{ label: string; description?: string }>
}

// ---------- Usage ---------------------------------------------------------

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

// ---------- Builder / Assistant -------------------------------------------

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

export interface AssistantToolCallResult {
  tool_name: string
  success: boolean
  summary: string
}

// ---------- Trigger -------------------------------------------------------

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

// ---------- Legacy aliases (transitional — kept so existing visual-settings
// components continue to compile until they migrate to domain-specific types) ---

import type { ToolInstance } from './tool'
import type { ModelCatalogEntry } from '@/lib/api/models'

/**
 * @deprecated Use `ToolInstance` from `@/lib/types/tool` directly.
 */
export type Tool = ToolInstance

/**
 * @deprecated Use `ModelCatalogEntry` from `@/lib/api/models` directly.
 */
export type Model = ModelCatalogEntry

// ---------- Middleware (catalog item from /api/middlewares) ----------------

export interface MiddlewareRegistryItem {
  type: string
  name: string
  display_name: string
  description: string
  category: 'context' | 'planning' | 'safety' | 'reliability' | 'provider' | string
  config_schema: Record<string, unknown>
  provider_specific: string | null
}
