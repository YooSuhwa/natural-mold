/** Workflows (feature plan v2 순번 23): the `moldy.workflow.v1` definition and the API shapes. */

export type WorkflowNodeType =
  | 'start'
  | 'agent'
  | 'decision_bool'
  | 'approval'
  | 'transform'
  | 'output'
  | 'fail'

export type EdgeWhen = 'true' | 'false' | 'approved' | 'rejected'

/** A template is a list of segments: literal text or a reference to an earlier value. */
export type TextSegment = { text: string }
export type RefSegment = { ref: { node: string; path: string } }
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
  | (NodeBase & { type: 'decision_bool'; condition: Condition })
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

export interface WorkflowEdge {
  from: string
  to: string
  when?: EdgeWhen | null
}

export interface WorkflowInput {
  name: string
  type: 'string' | 'number' | 'integer' | 'boolean'
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
  position: number
  node_id: string
  node_type: WorkflowNodeType
  status: WorkflowStepStatus
  branch: EdgeWhen | null
  output: Record<string, unknown> | null
  error_code: string | null
  error_message: string | null
  conversation_id: string | null
  started_at: string | null
  finished_at: string | null
}

export interface WorkflowRunDetail extends WorkflowRunSummary {
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
