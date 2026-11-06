'use client'

import { useTranslations } from 'next-intl'

import { ApiError } from '@/lib/api/errors'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import type {
  GuardrailAction,
  GuardrailStage,
  ToolRiskAction,
  ToolRiskLevel,
} from '@/lib/types/guardrails'

export const GUARDRAIL_ACTIONS: readonly GuardrailAction[] = [
  'block',
  'redact',
  'hash',
  'pseudonymize',
  'mask',
  'warn',
]
export const GUARDRAIL_STAGES: readonly GuardrailStage[] = [
  'llm_send',
  'tool_args',
  'tool_result',
  'output',
]

export const TOOL_RISK_LEVELS: readonly ToolRiskLevel[] = [
  'read_only',
  'write_internal',
  'external_mutation',
  'code_execution',
  'unknown',
]
export const TOOL_RISK_ACTIONS: readonly ToolRiskAction[] = ['allow', 'approval', 'block']
/** The server's defaults (`app/guardrails/tool_risk.py`): read-only runs, the rest asks. */
export const DEFAULT_TOOL_RISK: Readonly<Record<ToolRiskLevel, ToolRiskAction>> = {
  read_only: 'allow',
  write_internal: 'approval',
  external_mutation: 'approval',
  code_execution: 'approval',
  unknown: 'approval',
}

/** Built-in rule ids get a readable name; a company's own rule shows its id. */
export function useRuleLabel(): (ruleId: string) => string {
  const t = useTranslations('guardrails.rule')
  return (ruleId) => (t.has(ruleId) ? t(ruleId) : ruleId)
}

/** Guardrail error codes first, then the shared company-admin copy. */
export function useGuardrailErrorMessage(): (error: unknown) => string {
  const t = useTranslations('guardrails.errors')
  const fallback = useOrgErrorMessage()
  return (error) =>
    error instanceof ApiError && t.has(error.code) ? t(error.code) : fallback(error)
}
