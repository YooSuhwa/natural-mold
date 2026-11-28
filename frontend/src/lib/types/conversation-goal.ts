export type GoalCriterion =
  | { kind: 'nonempty_file'; logical_path: string }
  | {
      kind: 'csv_table'
      logical_path: string
      required_columns: string[]
      unique_by: string
      minimum_rows: number
      column_equals: Record<string, string>
      reference_rows?: Record<string, string>[]
    }
  | { kind: 'manual'; description: string }

export type GoalAction = 'pause' | 'resume' | 'cancel'
export type GoalStatus = 'active' | 'paused' | 'blocked' | 'completed' | 'canceled'

export interface GoalEvidenceCheck {
  criterion_index: number
  passed: boolean
  reason: string
  source_run_id?: string | null
  artifact_id: string | null
  version_id: string | null
  version_number: number | null
  sha256: string | null
  logical_path: string | null
}

export interface ConversationGoal {
  id: string
  title: string
  criteria: GoalCriterion[]
  version: number
  revision: number
  status: GoalStatus
  auto_followups: number
  blocked_reason: string | null
  last_evidence: { all_passed: boolean; progress: number; checks: GoalEvidenceCheck[] } | null
}

export interface GoalEnvelope {
  goal: ConversationGoal | null
  enabled: boolean
}

export interface SaveGoalInput {
  expected_revision: number
  title: string
  criteria: GoalCriterion[]
}

export interface GoalHistoryReceipt {
  conversationId: string
  goalId: string
  goalVersion: number
  artifactId: string
  versionId: string
  producingRunId: string
  sha256: string
}
