/** Guardrail policy, templates and agent choice (backend `app/schemas/guardrails.py`). */

export type GuardrailPolicyMode = 'off' | 'open' | 'default' | 'enforced'
export type GuardrailStage = 'input' | 'llm_send' | 'tool_args' | 'tool_result' | 'output'
export type GuardrailAction = 'block' | 'redact' | 'hash' | 'pseudonymize' | 'mask' | 'warn'
export type GuardrailPseudonymStyle = 'tag' | 'natural'
export type GuardrailTemplateStatus = 'draft' | 'published' | 'archived'
export type ToolRiskLevel =
  | 'read_only'
  | 'write_internal'
  | 'external_mutation'
  | 'code_execution'
  | 'unknown'
export type ToolRiskAction = 'allow' | 'approval' | 'block'
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
