/** Workflows (feature plan v2 순번 23): the `moldy.workflow.v1` definition and the API shapes. */

export type WorkflowNodeType =
  | 'start'
  | 'agent'
  | 'decision_bool'
  | 'approval'
  | 'transform'
  | 'output'
  | 'fail'
  | 'switch'
  | 'llm'
  | 'ask'
  | 'notify'
  | 'for_each'
  | 'parallel'
  | 'tool'

/** The fixed exits; ``limit`` is a loop's exit once its rounds run out (workflow-loop-plan). */
export type FixedExit =
  | 'true'
  | 'false'
  | 'approved'
  | 'rejected'
  | 'limit'
  | 'answered'
  | 'timeout'
  | 'default'
/** A fixed exit, or a switch case name (workflow-nodes-plan 3.2). */
export type EdgeWhen = FixedExit | (string & {})

export const FIXED_EXITS: readonly FixedExit[] = [
  'true',
  'false',
  'approved',
  'rejected',
  'limit',
  'answered',
  'timeout',
  'default',
]

/** A template is a list of segments: literal text or a reference to an earlier value. */
export type TextSegment = { text: string }
/** ``previous``: in a loop, the last round's value of a later step (workflow-loop-plan 3.1). */
export type RefSegment = { ref: { node: string; path: string; previous?: boolean } }
export type TemplateSegment = TextSegment | RefSegment

export type ClauseOp = 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'contains' | 'exists'

export interface Clause {
  source: string
  path: string
  op: ClauseOp
  value?: string | number | boolean | null
}

export interface Condition {
  all?: Clause[] | null
  any?: Clause[] | null
}

interface NodeBase {
  id: string
  label?: string | null
}

export type WorkflowNode =
  | (NodeBase & { type: 'start' })
  | (NodeBase & {
      type: 'agent'
      agent_id: string
      prompt: TemplateSegment[]
      output_schema: Record<string, unknown>
    })
  | (NodeBase & {
      type: 'decision_bool'
      /** ``rule`` compares values (``condition``); ``llm`` asks the system model ``question``. */
      evaluator?: 'rule' | 'llm'
      condition?: Condition | null
      question?: TemplateSegment[] | null
      /** Set when an exit leads back (a loop); rounds include the first. */
      repeat?: { max_rounds: number } | null
    })
  | (NodeBase & {
      type: 'approval'
      title: TemplateSegment[]
      body?: TemplateSegment[]
      timeout_minutes?: number | null
    })
  | (NodeBase & {
      type: 'transform'
      template?: TemplateSegment[]
      fields?: Record<string, TemplateSegment[]>
    })
  | (NodeBase & { type: 'output'; fields: Record<string, TemplateSegment[]> })
  | (NodeBase & { type: 'fail'; reason?: string })
  | (NodeBase & {
      type: 'switch'
      source: string
      path: string
      cases: SwitchCase[]
    })
  | (NodeBase & { type: 'llm'; prompt: TemplateSegment[]; output_schema: Record<string, unknown> })
  | (NodeBase & { type: 'ask'; question: TemplateSegment[]; timeout_minutes?: number | null })
  | (NodeBase & { type: 'notify'; title: TemplateSegment[]; body?: TemplateSegment[] })
  | (NodeBase & {
      type: 'for_each'
      source: string
      path: string
      agent_id: string
      prompt: TemplateSegment[]
      output_schema: Record<string, unknown>
      max_items?: number
    })
  | (NodeBase & { type: 'parallel'; branches: ParallelBranch[] })
  | (NodeBase & {
      type: 'tool'
      tool: WorkflowToolName
      args?: Record<string, TemplateSegment[]>
    })

export interface SwitchCase {
  name: string
  value: string | number | boolean
}

export interface ParallelBranch {
  name: string
  agent_id: string
  prompt: TemplateSegment[]
  output_schema: Record<string, unknown>
}

export type WorkflowToolName = 'web_search' | 'web_scraper' | 'current_datetime'

/** The arguments each read-only tool takes (workflow-nodes-plan N7). */
export const TOOL_ARGS: Record<WorkflowToolName, readonly string[]> = {
  web_search: ['query'],
  web_scraper: ['url'],
  current_datetime: [],
}

export const MAX_FOR_EACH = 20
export const MAX_PARALLEL = 5
export const MAX_CASES = 10

export interface WorkflowEdge {
  from: string
  to: string
  when?: EdgeWhen | null
}

export interface WorkflowInput {
  name: string
  /** ``file``: an upload id; agent steps that reference it get the file attached. */
  type: 'string' | 'number' | 'integer' | 'boolean' | 'file'
  required: boolean
  label?: string | null
  max_length?: number | null
}

export interface WorkflowDefinition {
  schema_version: 'moldy.workflow.v1'
  inputs: WorkflowInput[]
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

export interface WorkflowSummary {
  id: string
  name: string
  description: string | null
  draft_revision: number
  published_version_no: number | null
  archived_at: string | null
  created_at: string
  updated_at: string
}

export interface WorkflowDetail extends WorkflowSummary {
  draft_definition: WorkflowDefinition
}

export interface WorkflowIssue {
  code: string
  message: string
  node_id: string | null
}

export interface WorkflowIssues {
  draft_revision: number
  issues: WorkflowIssue[]
}

export interface WorkflowVersionSummary {
  version_no: number
  definition_hash: string
  published_by: string | null
  published_at: string
}

export interface WorkflowVersionDetail extends WorkflowVersionSummary {
  definition: WorkflowDefinition
  agent_pins: Record<string, { agent_id: string; fingerprint: string }>
}

export type WorkflowRunStatus =
  | 'queued'
  | 'running'
  | 'waiting'
  | 'unknown'
  | 'completed'
  | 'failed'
  | 'canceling'
  | 'canceled'

export interface WorkflowRunSummary {
  id: string
  workflow_id: string
  version_id: string
  version_no: number | null
  status: WorkflowRunStatus
  error_code: string | null
  error_message: string | null
  current_node: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  /** Started by a chat's workflow tool: the conversation it came from (workflow-tool-plan). */
  parent_conversation_id?: string | null
  /** How it started (workflow-starts-sharing-plan). */
  started_from?: WorkflowRunStart
}

export type WorkflowRunStart = 'manual' | 'chat_tool' | 'schedule' | 'webhook'

/** A schedule: fixed inputs at a time or interval; its maker is the runner. */
export interface WorkflowSchedule {
  id: string
  workflow_id: string
  name: string
  kind: 'interval' | 'cron' | 'once'
  config: { minutes?: number; expression?: string; at?: string }
  timezone: string
  inputs: Record<string, unknown>
  status: 'active' | 'paused'
  next_run_at: string | null
  last_run_at: string | null
  last_run_id: string | null
  failure_count: number
  last_error: string | null
  created_at: string
}

export type WorkflowScheduleInput = Pick<
  WorkflowSchedule,
  'name' | 'kind' | 'config' | 'timezone' | 'inputs'
>

/** A webhook an outside system starts runs with (its token is shown once). */
export interface WorkflowWebhook {
  id: string
  workflow_id: string
  name: string
  public_id: string
  token_hint: string
  status: 'active' | 'disabled'
  last_used_at: string | null
  created_at: string
}

export interface WorkflowWebhookCreated extends WorkflowWebhook {
  token: string
}

/** A published workflow an agent may call as a chat tool (workflow-tool-plan 3.1). */
export interface AgentWorkflowTool {
  workflow_id: string
  name: string
  description: string | null
  published_version_no: number | null
  usable: boolean
  broken_reason: string | null
}

export type WorkflowStepStatus =
  | 'queued'
  | 'running'
  | 'waiting'
  | 'completed'
  | 'failed'
  | 'unknown'
  | 'canceled'

export interface WorkflowStepRun {
  id: string
  position: number
  node_id: string
  node_type: WorkflowNodeType
  /** 1, then +1 each time a loop runs the node again. */
  round: number
  status: WorkflowStepStatus
  branch: EdgeWhen | null
  output: Record<string, unknown> | null
  error_code: string | null
  error_message: string | null
  conversation_id: string | null
  started_at: string | null
  finished_at: string | null
  /** A child row: one agent of a for_each/parallel step (item index or branch name). */
  parent_step_id?: string | null
  branch_key?: string | null
}

export interface WorkflowRunDetail extends WorkflowRunSummary {
  /** The agent of the chat that started it, for the link back (workflow-tool-plan 3.5). */
  parent_agent_id?: string | null
  input: Record<string, unknown>
  output: Record<string, unknown> | null
  steps: WorkflowStepRun[]
}

export interface WorkflowAgentUsage {
  id: string
  name: string
  published_version_no: number | null
  in_draft: boolean
  in_published: boolean
  drifted: boolean
}

export const RUN_TERMINAL: readonly WorkflowRunStatus[] = ['completed', 'failed', 'canceled']

/** AI editing (W5): a question back, or a proposed draft that is not saved yet. */
export interface WorkflowAssistTurn {
  role: 'user' | 'assistant'
  text: string
}

export interface WorkflowAssistResponse {
  kind: 'question' | 'proposal' | 'failed'
  message: string
  plan: string[]
  definition: WorkflowDefinition | null
  issues: WorkflowIssue[]
  base_revision: number
}
