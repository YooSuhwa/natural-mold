// Re-exports for greenfield domain types. Legacy types (Connection, Provider,
// CredentialFieldDef, etc.) were removed alongside the routers they mirrored.

export * from './credential'
export * from './tool'
export * from './mcp'
export * from './skill'
export * from './model'
export * from './health'
export * from './usage'

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

export interface McpToolBrief {
  id: string
  name: string
  server_id: string
  server_name?: string | null
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
  // Nullable to mirror the backend graceful response — agents whose
  // model_id FK target was deleted out from under them still serialize
  // (rather than crashing the whole list). UI surfaces "no model bound"
  // and prompts re-binding instead of throwing on agent.model.x access.
  model: ModelBrief | null
  tools: ToolBrief[]
  mcp_tools: McpToolBrief[]
  skills: SkillBrief[]
  sub_agents: AgentBrief[]
  status: string
  is_favorite: boolean
  model_params: ModelParams | null
  middleware_configs: MiddlewareConfigEntry[]
  template_id: string | null
  created_at: string
  updated_at: string
  /** Most recent conversation activity (max(conv.updated_at)). Set by the
   * list endpoint only — single-row responses leave this null. Sidebar uses
   * this with a fallback to ``updated_at`` so chatting floats agents up. */
  last_used_at?: string | null
  image_url: string | null
  opener_questions: string[] | null
  llm_credential_id?: string | null
  unread_count: number
  /**
   * M10 — fallback model UUIDs tried in order when the primary model fails.
   * Backend column is `agents.model_fallback_list` (Postgres ARRAY of UUID).
   */
  model_fallback_ids?: string[] | null
}

export interface AgentCreateRequest {
  name: string
  description?: string
  system_prompt: string
  model_id: string
  tool_ids?: string[]
  mcp_tool_ids?: string[]
  skill_ids?: string[]
  sub_agent_ids?: string[]
  template_id?: string
  model_params?: ModelParams
  middleware_configs?: MiddlewareConfigEntry[]
  opener_questions?: string[]
  llm_credential_id?: string | null
  model_fallback_ids?: string[] | null
}

export interface AgentUpdateRequest {
  name?: string
  description?: string
  system_prompt?: string
  model_id?: string
  tool_ids?: string[]
  mcp_tool_ids?: string[]
  skill_ids?: string[]
  sub_agent_ids?: string[]
  is_favorite?: boolean
  model_params?: ModelParams
  middleware_configs?: MiddlewareConfigEntry[]
  opener_questions?: string[]
  llm_credential_id?: string | null
  model_fallback_ids?: string[] | null
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
  unread_count: number
  last_read_at: string | null
  last_unread_at: string | null
  last_activity_source: string
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

export interface MessageFeedbackBrief {
  rating: 'up' | 'down'
}

export interface MessageAttachmentBrief {
  id: string
  filename: string
  mime_type: string
  size_bytes: number
  url: string
}

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  tool_calls: ToolCallInfo[] | null
  tool_call_id: string | null
  created_at: string
  feedback?: MessageFeedbackBrief | null
  attachments?: MessageAttachmentBrief[] | null
  /**
   * M-CHAT1b — parent message id in the LangGraph branch tree.
   * `null` for the very first message. Used to build assistant-ui's
   * `messageRepository` so the BranchPicker auto-detects siblings.
   */
  parent_id?: string | null
  /** LangGraph checkpoint id this message was first emitted from. Sent back
   * via `/switch-branch` when the user picks a sibling. */
  branch_checkpoint_id?: string | null
  /** Sibling message ids (same role, same parent). The active message id is
   * always included. Empty/length-1 when this message has no siblings. */
  siblings?: string[]
  /** Per-sibling checkpoint ids — same order as ``siblings``. Frontend posts
   * the chosen sibling's checkpoint_id to ``/switch-branch`` to flip the
   * active branch. */
  sibling_checkpoint_ids?: string[]
  /** M-CHAT1b HOTFIX2 — 0-based position of *this* (active) message inside
   * ``siblings``. Backend sorts siblings oldest→newest by checkpoint id, so
   * BranchPicker just renders ``<branch_index+1 / branch_total>`` directly
   * instead of indexOf'ing the active id. ``null`` for messages with no
   * siblings. */
  branch_index?: number | null
  branch_total?: number | null
  /** W7 — assistant 메시지 끝(``message_end``)에서 채워지는 토큰 사용량.
   * 4종 분리: input/output 외에 cache_creation/cache_read까지. 메시지 푸터의
   * hover 팝오버가 직접 참조한다. 백엔드가 발행하지 않거나 user/tool 메시지
   * 인 경우 ``null``. */
  usage?: TokenUsageBreakdown | null
}

/** W7 — 메시지별 토큰 사용량 4종 분해. */
export interface TokenUsageBreakdown {
  prompt_tokens: number
  completion_tokens: number
  cache_creation_tokens: number
  cache_read_tokens: number
  estimated_cost?: number
}

/**
 * Envelope returned by `GET /api/conversations/:id/messages` post-M-CHAT1b.
 * Wraps the message list with branch/active-tip metadata.
 */
export interface MessagesEnvelope {
  messages: Message[]
  active_tip_message_id?: string | null
  active_checkpoint_id?: string | null
  /** W7-4 — conversation 누적 비용 (USD). ``token_usages`` 테이블 합산. 메시지
   * 단위로 cost를 채울 수 없는 fetch 경로(model_id 없음)에서 Composer 토큰 바
   * 가 cost를 표시할 수 있게 envelope에 발행. */
  total_estimated_cost?: number
}

export interface MessageFeedbackRow {
  id: string
  message_id: string
  conversation_id: string
  rating: 'up' | 'down'
  comment: string | null
  created_at: string
}

export interface UploadResponse {
  id: string
  filename: string
  mime_type: string
  size_bytes: number
  url: string
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
  | 'stale'

// ── HiTL — interrupt wire (LangChain HumanInTheLoopMiddleware 표준) ──────

/** `HITLRequest.action_requests[i]` 한 항목. */
export interface ActionRequest {
  name: string
  args: Record<string, unknown>
  description?: string
}

export type DecisionType = 'approve' | 'edit' | 'reject' | 'respond'

/** `HITLRequest.review_configs[i]` — 도구별 허용 결정 화이트리스트. */
export interface ReviewConfig {
  action_name: string
  allowed_decisions: DecisionType[]
}

/** SSE `interrupt` event payload — `HITLRequest` + correlation `interrupt_id`. */
export interface StandardInterruptPayload {
  interrupt_id: string
  action_requests: ActionRequest[]
  review_configs: ReviewConfig[]
}

export type InterruptPayload = StandardInterruptPayload

/** Resume 송신용 단일 결정. LangChain `HITLResponse.decisions[i]`와 1:1. */
export interface Decision {
  type: DecisionType
  /** type='edit' 시 필수: 수정된 tool_call. */
  edited_action?: { name: string; args: Record<string, unknown> }
  /** type='respond' 시 필수, type='reject' 시 선택. */
  message?: string
}

/** POST `/conversations/:id/messages/resume` 표준 body. */
export interface ResumeDecisionsRequest {
  decisions: Decision[]
}

// W3-out M3 — backend 가 broker 손실 (in-flight turn 중 backend 가 죽어 GET
// resume 이 DB replay 만 받은 케이스) 을 client 에 알리는 marker. ``reason``
// = ``broker_lost`` (events 에 last_event_id 있음) 또는 ``broker_lost_no_id``
// (events 자체가 빈 채로 status='streaming' row 만 있음 — NPE 회피용 구분).
export interface StalePayload {
  reason: 'broker_lost' | 'broker_lost_no_id'
  last_event_id: string | null
}

// ``id``: 백엔드가 발행하는 SSE id (``{msg_id}-{seq}`` 형식). caller side에서
// dedup/stale 폐기에 사용한다. 모든 variant에 공통으로 optional.
export type SSEEvent = { id?: string } & (
  | { event: 'message_start'; data: { id: string; role: string } }
  | { event: 'content_delta'; data: { delta?: string; content?: string } }
  | { event: 'tool_call_start'; data: { tool_name: string; parameters: Record<string, unknown> } }
  | { event: 'tool_call_result'; data: { tool_name: string; result: string } }
  | {
      event: 'message_end'
      data: {
        content: string
        // W7 — usage 4종(input/output/cache_creation/cache_read) + 선택적 비용.
        // 비어 있을 수 있어 모든 필드 optional로 둔다.
        usage: Partial<TokenUsageBreakdown> & Record<string, number>
      }
    }
  | { event: 'error'; data: { message: string } }
  | { event: 'interrupt'; data: InterruptPayload }
  | { event: 'stale'; data: StalePayload }
)

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
  name: string
  trigger_type: 'interval' | 'cron' | 'one_time'
  schedule_config: { interval_minutes?: number; cron_expression?: string; scheduled_at?: string }
  input_message: string
  timezone: string
  conversation_policy: string
  schedule_conversation_id: string | null
  status: 'active' | 'paused' | 'completed' | 'error'
  last_run_at: string | null
  next_run_at: string | null
  last_status: 'running' | 'success' | 'failed' | 'skipped' | null
  last_error: string | null
  run_count: number
  failure_count: number
  max_runs: number | null
  end_at: string | null
  auto_pause_after_failures: number | null
  created_at: string
  updated_at: string
  agent_name?: string | null
  schedule_conversation_title?: string | null
  schedule_conversation_unread_count?: number
}

export interface TriggerCreateRequest {
  name?: string
  trigger_type: 'interval' | 'cron' | 'one_time'
  schedule_config: Record<string, unknown>
  input_message: string
  timezone?: string
  conversation_policy?: string
  max_runs?: number | null
  end_at?: string | null
  auto_pause_after_failures?: number | null
}

export interface TriggerUpdateRequest {
  name?: string
  trigger_type?: AgentTrigger['trigger_type']
  schedule_config?: Record<string, unknown>
  input_message?: string
  timezone?: string
  conversation_policy?: string
  status?: AgentTrigger['status']
  max_runs?: number | null
  end_at?: string | null
  auto_pause_after_failures?: number | null
}

export interface TriggerRun {
  id: string
  trigger_id: string
  agent_id: string
  user_id: string
  conversation_id: string | null
  status: 'running' | 'success' | 'failed' | 'skipped'
  input_message: string
  error_message: string | null
  started_at: string
  finished_at: string | null
  created_at: string
}

export interface TriggerSummary {
  total_unread: number
  active_count: number
}

// ---------- Legacy aliases (transitional — kept so existing visual-settings
// components continue to compile until they migrate to domain-specific types) ---

import type { ToolInstance } from './tool'

/**
 * @deprecated Use `ToolInstance` from `@/lib/types/tool` directly.
 */
export type Tool = ToolInstance

// `Model` is now exported from `./model` (M7 catalog overhaul). The legacy
// alias to `ModelCatalogEntry` was removed since the canonical type lives in
// `./model.ts` and is already re-exported above.

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
