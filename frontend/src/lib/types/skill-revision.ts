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
  readonly parent_revision_id?: string | null
  readonly restored_from_revision_id?: string | null
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

/** 리비전 스냅샷 파일 목록 (Phase 2 — 버전 diff/소스 보기). */
export type SkillRevisionFileEntry = {
  readonly path: string
  readonly size: number
  /** 앞 8KB sniff에 널바이트 — 내용 조회는 404(fail-closed). */
  readonly is_binary: boolean
}

export type SkillRevisionFilesResponse = {
  /** 리텐션이 스냅샷을 정리한 리비전 — 파일 목록/내용 없음. */
  readonly snapshot_pruned: boolean
  readonly files: readonly SkillRevisionFileEntry[]
}

export type SkillRevisionFileContent = {
  readonly path: string
  readonly content: string
}
