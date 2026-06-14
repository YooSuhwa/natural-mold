import type { Skill } from './skill'
import type { JsonValue } from './json'

export type SkillRevisionOperation =
  | 'create'
  | 'manual_metadata_update'
  | 'manual_content_update'
  | 'manual_file_update'
  | 'builder_create'
  | 'builder_improvement'
  | 'rollback'

export type SkillRevisionSummary = {
  readonly id: string
  readonly skill_id: string
  readonly revision_number: number
  readonly operation: SkillRevisionOperation
  readonly skill_version?: string | null
  readonly content_hash?: string | null
  readonly size_bytes: number
  readonly file_count: number
  readonly changelog_summary?: string | null
  readonly created_at: string
}

export type SkillRevisionDetail = SkillRevisionSummary & {
  readonly changed_files?: readonly JsonValue[] | null
  readonly changelog_items?: readonly JsonValue[] | null
  readonly compatibility_result?: Readonly<Record<string, JsonValue>> | null
  readonly evaluation_summary?: Readonly<Record<string, JsonValue>> | null
  readonly metadata_json: Readonly<Record<string, JsonValue>>
}

export type SkillRollbackResponse = {
  readonly skill: Skill
  readonly revision: SkillRevisionSummary
}
