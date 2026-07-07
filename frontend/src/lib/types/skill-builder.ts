import type { JsonValue } from './json'

export type SkillBuilderMode = 'create' | 'improve'

export type SkillBuilderStatus =
  | 'collecting'
  | 'drafting'
  | 'review'
  | 'confirming'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type SkillDraftFileRole = 'skill' | 'script' | 'reference' | 'asset' | 'metadata' | 'eval'

export type SkillDraftFile = {
  readonly path: string
  readonly content: string
  readonly media_type: string
  readonly role: SkillDraftFileRole
}

export type SkillValidationIssueSeverity = 'error' | 'warning' | 'info'

export type SkillValidationIssue = {
  readonly code: string
  readonly severity: SkillValidationIssueSeverity
  readonly path?: string | null
  readonly message: string
}

export type SkillCompatibilityTarget = {
  readonly status: 'pass' | 'warning' | 'error'
  readonly issues: readonly SkillValidationIssue[]
}

export type SkillCompatibilityResult = {
  readonly targets?: Readonly<Record<string, SkillCompatibilityTarget>>
  readonly error_count?: number
  readonly warning_count?: number
  readonly info_count?: number
  readonly [key: string]: JsonValue | Readonly<Record<string, SkillCompatibilityTarget>> | undefined
}

export type SkillDraftPackage = {
  readonly name: string
  readonly slug: string
  readonly description: string
  readonly files: readonly SkillDraftFile[]
  readonly credential_requirements: readonly JsonValue[]
  readonly execution_profile: Readonly<Record<string, JsonValue>>
  readonly validation_issues: readonly JsonValue[]
  readonly compatibility_result?: SkillCompatibilityResult | null
  readonly changelog_draft?: Readonly<Record<string, JsonValue>> | null
  readonly evals?: Readonly<Record<string, JsonValue>> | null
  readonly benchmark?: Readonly<Record<string, JsonValue>> | null
}

export type SkillBuilderStartRequest = {
  readonly mode: SkillBuilderMode
  readonly user_request: string
  readonly source_skill_id?: string | null
}

export type SkillBuilderSession = {
  readonly id: string
  readonly user_id: string
  readonly user_request: string
  readonly mode: SkillBuilderMode
  readonly status: SkillBuilderStatus
  readonly current_phase: number
  readonly source_skill_id?: string | null
  readonly base_skill_version?: string | null
  readonly base_content_hash?: string | null
  readonly base_snapshot?: Readonly<Record<string, JsonValue>> | null
  readonly messages?: readonly JsonValue[] | null
  readonly intent?: Readonly<Record<string, JsonValue>> | null
  readonly draft_package?: SkillDraftPackage | null
  readonly validation_result?: Readonly<Record<string, JsonValue>> | null
  readonly compatibility_result?:
    | SkillCompatibilityResult
    | Readonly<Record<string, JsonValue>>
    | null
  readonly changelog_draft?: Readonly<Record<string, JsonValue>> | null
  readonly eval_result?: Readonly<Record<string, JsonValue>> | null
  readonly trigger_eval_result?: Readonly<Record<string, JsonValue>> | null
  readonly finalized_skill_id?: string | null
  // v2 (빌더 챗): 히든 에이전트의 진짜 conversation. agent_id는 대화 역참조로
  // 백엔드 라우터가 채운다 — /skills/builder/[sessionId]가 ChatRuntimeSection
  // 마운트에 사용.
  readonly conversation_id?: string | null
  readonly agent_id?: string | null
  readonly error_message?: string | null
  readonly created_at: string
  readonly updated_at: string
}
