import type { JsonValue } from './json'

export type SkillEvaluationRunStatus =
  | 'queued'
  | 'running'
  | 'grading'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type SkillHealthState =
  | 'ready'
  | 'needs_evaluation'
  | 'needs_rerun'
  | 'needs_credentials'
  | 'evaluation_running'
  | 'evaluation_failed'
  | 'low_confidence'

export type SkillHealthSeverity = 'success' | 'info' | 'warning' | 'error' | 'neutral'

export type SkillHealthSummary = {
  readonly state: SkillHealthState
  readonly label: string
  readonly reason: string
  readonly severity: SkillHealthSeverity
}

export type SkillEvaluationRunEstimate = {
  readonly case_count: number
  readonly model_call_count: number
  readonly estimated_seconds: number
  readonly timeout_seconds: number
  readonly estimated_cost_usd: number
  readonly uses_baseline_comparison: boolean
}

export type SkillEvaluationRun = {
  readonly id: string
  readonly skill_id: string
  readonly evaluation_set_id: string
  readonly status: SkillEvaluationRunStatus
  readonly skill_version?: string | null
  readonly skill_content_hash?: string | null
  readonly runner_model?: string | null
  readonly summary?: Readonly<Record<string, JsonValue>> | null
  readonly benchmark?: Readonly<Record<string, JsonValue>> | null
  readonly case_results?: readonly JsonValue[] | null
  readonly error_message?: string | null
  readonly cancellation_requested_at?: string | null
  readonly cancellation_reason?: string | null
  readonly started_at?: string | null
  readonly completed_at?: string | null
  readonly created_at: string
  readonly updated_at: string
}

export type SkillLatestEvaluationSummary = {
  readonly status: SkillEvaluationRunStatus | 'missing' | 'stale' | 'partial' | 'passed'
  readonly latest_run_id?: string | null
  readonly evaluation_set_id?: string | null
  readonly pass_rate?: number | null
  readonly skill_content_hash?: string | null
  readonly created_at?: string | null
  readonly completed_at?: string | null
}

export type SkillEvaluationSet = {
  readonly id: string
  readonly skill_id: string
  readonly name: string
  readonly description?: string | null
  readonly source_kind: string
  readonly evals: readonly JsonValue[]
  readonly expectations_schema_version: number
  readonly latest_run?: SkillEvaluationRun | null
  readonly created_at: string
  readonly updated_at: string
}

export type SkillEvaluationSetCreate = {
  readonly name: string
  readonly description?: string | null
  readonly evals: readonly JsonValue[]
}

export type SkillEvaluationSetUpdate = {
  readonly name?: string | null
  readonly description?: string | null
  readonly evals?: readonly JsonValue[] | null
}

export type SkillEvaluationRunCancelRequest = {
  readonly reason?: string
}
