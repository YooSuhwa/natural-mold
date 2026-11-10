/** Guardrail policy, templates and agent choice (backend `app/schemas/guardrails.py`). */
import type { ToolApprovalRule } from './runtime-policy'

export type GuardrailPolicyMode = 'off' | 'open' | 'default' | 'enforced'
export type GuardrailStage = 'input' | 'llm_send' | 'tool_args' | 'tool_result' | 'output'
export type GuardrailAction =
  | 'block'
  | 'approval'
  | 'redact'
  | 'hash'
  | 'pseudonymize'
  | 'mask'
  | 'warn'
export type GuardrailPseudonymStyle = 'tag' | 'natural'
export type GuardrailTemplateStatus = 'draft' | 'published' | 'archived'
export type ToolRiskLevel =
  | 'read_only'
  | 'write_internal'
  | 'external_mutation'
  | 'code_execution'
  | 'unknown'
/** ``approval_relaxable``: asked by default, but an agent's tool approval settings may lift it. */
export type ToolRiskAction = 'allow' | 'approval' | 'approval_relaxable' | 'block'
/** Every risk level with the company's action (defaults filled in by the server). */
export type ToolRiskActions = Record<ToolRiskLevel, ToolRiskAction>

export interface GuardrailRule {
  id: string
  packs: string[]
  stages: GuardrailStage[]
  action: GuardrailAction
  entities: string[] | null
  restore_for_tools: boolean
  pseudonym_style: GuardrailPseudonymStyle
}

/** A template a policy or an agent can point at: `builtin:<name>` or `company:<uuid>`. */
export interface GuardrailTemplateChoice {
  ref: string
  name: string
  kind: 'builtin' | 'company'
  version: number
  rules: GuardrailRule[]
}

export interface GuardrailPolicy {
  mode: GuardrailPolicyMode
  default_template: string | null
  locked_rules: string[]
  tool_risk: ToolRiskActions
  /** Tools a person always approves, whatever an agent's settings say. */
  always_ask_tools?: string[]
  /** False: the company has not saved a policy; the installation default applies. */
  saved: boolean
  templates: GuardrailTemplateChoice[]
  /** The company version (`If-Match`). */
  updated_at: string
}

export interface GuardrailPolicyUpdate {
  mode: GuardrailPolicyMode
  default_template: string | null
  locked_rules: string[]
  /** Omitted: the saved actions stay. */
  tool_risk?: ToolRiskActions
  /** Omitted: the saved list stays. */
  always_ask_tools?: string[]
}

export interface GuardrailTemplate {
  id: string
  ref: string
  name: string
  description: string
  status: GuardrailTemplateStatus
  draft_rules: GuardrailRule[]
  published_version: number | null
  updated_at: string
}

/** One published version; never changes after publishing. */
export interface GuardrailTemplateVersion {
  version: number
  rules: GuardrailRule[]
  published_at: string
  published_by: string | null
}

export interface GuardrailTemplateCreate {
  name: string
  description?: string
  copy_from: string
}

export interface GuardrailTemplateUpdate {
  name?: string
  description?: string
  rules?: GuardrailRule[]
}

export type AgentGuardrailMode = 'inherit' | 'template' | 'off'

export interface GuardrailRuleOverride {
  enabled: boolean
  action: GuardrailAction | null
}

/** An agent's guardrail choice and what its next run gets (`/api/agents/{id}/guardrails`). */
export interface AgentGuardrails {
  mode: AgentGuardrailMode
  template_ref: string | null
  rule_overrides: Record<string, GuardrailRuleOverride>
  policy_mode: GuardrailPolicyMode
  /** The company default the agent inherits; overrides apply to its rules. */
  policy_default_template: string | null
  locked_rules: string[]
  /** The company's tool risk actions; the agent cannot change them. */
  tool_risk: ToolRiskActions
  /** `null`: the next run has no guardrail. */
  effective: GuardrailTemplateChoice | null
  templates: GuardrailTemplateChoice[]
}

export interface AgentGuardrailsUpdate {
  mode: AgentGuardrailMode
  template_ref: string | null
  rule_overrides: Record<string, GuardrailRuleOverride>
}

/** Company policy for one tool or MCP server (tool-governance-plan 3.4). */
export type ToolPolicyMode = 'always' | 'when' | 'never'

export interface ToolPolicyUpdate {
  /** `null`: the risk level decides; `false`: nobody can use it. */
  enabled: boolean | null
  default_mode: ToolPolicyMode | null
  /** `null`: every mode is allowed. */
  allowed_modes: ToolPolicyMode[] | null
  /** The condition of a `when` default. */
  rule: ToolApprovalRule | null
}

export interface ToolPolicy extends ToolPolicyUpdate {
  resource_key: string
  resource_name: string
  updated_at: string
}

export interface ToolPolicyWarning {
  code: 'risky_without_asking'
  risk_level?: string | null
}

export interface ToolPolicySaved {
  /** `null`: the policy was removed. */
  policy: ToolPolicy | null
  warnings: ToolPolicyWarning[]
}

export interface ToolPolicyTarget {
  key: string
  name: string
  kind: 'mcp_server' | 'mcp_tool' | 'tool' | 'builtin'
  server_key?: string | null
  /** The MCP server's name. */
  source?: string | null
  risk_level?: string | null
}

export interface ToolPolicyImpactAgent {
  id: string
  name: string
  tool_key: string
  tool_name: string
  mode: ToolPolicyMode
  reason: 'disabled' | 'mode_not_allowed'
}

export interface ToolPolicyImpact {
  agents: ToolPolicyImpactAgent[]
  total: number
  warnings: ToolPolicyWarning[]
}
