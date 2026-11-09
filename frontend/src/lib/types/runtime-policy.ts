/**
 * Portable, versioned runtime metadata owned by an agent.
 *
 * This intentionally excludes resolved provenance and conversation snapshots:
 * those values belong to the server-side execution boundary and must never be
 * copied through agent settings, marketplace, or deployment payloads.
 */
export interface RuntimePolicyFilesystemV1 {
  mode: 'inspect' | 'artifact_write'
}

export interface RuntimePolicyTodoV1 {
  enabled: boolean
}

export type RuntimePolicySummarizationV1 =
  | { mode: 'auto' }
  | { mode: 'preset'; preset: 'balanced_context_v1' }

export type ToolApprovalMode = 'ask_all' | 'auto'
export type ToolApprovalPath = 'chat' | 'trigger' | 'workflow' | 'api'

/** A rule over the call's args: ``{all|any: [{source: 'args', path, op, value}]}``. */
export interface ToolApprovalRule {
  all?: ToolApprovalClause[]
  any?: ToolApprovalClause[]
}

export interface ToolApprovalClause {
  source: 'args'
  path: string
  op:
    | 'eq'
    | 'ne'
    | 'gt'
    | 'gte'
    | 'lt'
    | 'lte'
    | 'contains'
    | 'exists'
    | 'starts_with'
    | 'ends_with'
  value?: string | number | boolean | null
}

export interface ToolApprovalToolV1 {
  mode: 'always' | 'never' | 'when'
  rule?: ToolApprovalRule
}

/** The agent's tool approval settings (approval-modes-plan 3.3); unset keeps today's asks. */
export interface ToolApprovalPolicyV1 {
  paths?: Partial<Record<ToolApprovalPath, ToolApprovalMode>>
  tools?: Record<string, ToolApprovalToolV1>
}

export interface RuntimePolicyV1 {
  version: 1
  filesystem: RuntimePolicyFilesystemV1
  todo: RuntimePolicyTodoV1
  summarization: RuntimePolicySummarizationV1
  tool_approval?: ToolApprovalPolicyV1
}
