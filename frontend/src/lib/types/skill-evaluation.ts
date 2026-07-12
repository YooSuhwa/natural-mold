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
  readonly estimated_tokens_in?: number
  readonly estimated_tokens_out?: number
  readonly estimated_cost_usd: number
  /** false → runner 모델 단가 미보유. cost 0은 "무료"가 아니라 "모름". */
  readonly pricing_available?: boolean
  readonly runner_model?: string | null
  readonly uses_baseline_comparison: boolean
}

/** llm-2 실측 usage rollup — 레거시/deterministic 런은 null. */
export type SkillEvaluationRunUsage = {
  readonly measured?: boolean
  readonly model_calls?: number
  readonly tokens_in?: number
  readonly tokens_out?: number
  readonly cost_usd?: number | null
}

export type SkillEvaluationVersionStats = {
  readonly skill_version?: string | null
  readonly content_hash?: string | null
  readonly run_count: number
  readonly latest_pass_rate?: number | null
  readonly avg_pass_rate?: number | null
  readonly latest_pass_rate_delta?: number | null
  readonly latest_measured: boolean
  readonly first_run_at: string
  readonly last_run_at: string
}

export type SkillCaseFeedbackVerdict = 'agree' | 'disagree'

export type SkillCaseFeedback = {
  readonly run_id: string
  readonly case_index: number
  readonly verdict: SkillCaseFeedbackVerdict
  readonly comment?: string | null
  readonly updated_at: string
}

export type SkillCaseFeedbackUpsert = {
  readonly case_index: number
  readonly verdict: SkillCaseFeedbackVerdict
  readonly comment?: string | null
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
  readonly usage?: SkillEvaluationRunUsage | null
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
