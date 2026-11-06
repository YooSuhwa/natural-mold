/** Guardrail policy, templates and agent choice (backend `app/schemas/guardrails.py`). */

export type GuardrailPolicyMode = 'off' | 'open' | 'default' | 'enforced'
export type GuardrailStage = 'llm_send' | 'tool_args' | 'tool_result' | 'output'
export type GuardrailAction = 'block' | 'redact' | 'hash' | 'pseudonymize' | 'mask' | 'warn'
export type GuardrailPseudonymStyle = 'tag' | 'natural'
export type GuardrailTemplateStatus = 'draft' | 'published' | 'archived'

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
