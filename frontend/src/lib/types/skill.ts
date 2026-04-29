// Skill domain types — mirrors backend `app/schemas/skill.py`.

export type SkillKind = 'text' | 'package'

export interface Skill {
  id: string
  name: string
  slug: string
  description: string | null
  kind: SkillKind
  version: string | null
  storage_path: string | null
  content_hash: string | null
  size_bytes: number
  used_by_count: number
  package_metadata: Record<string, unknown> | null
  last_modified_at: string
  created_at: string
  updated_at: string
}

export interface SkillFileEntry {
  path: string
  size: number
  is_dir: boolean
}

export interface SkillCreateRequest {
  name: string
  slug?: string | null
  description?: string | null
  content: string
  version?: string | null
}

export interface SkillMetadataUpdateRequest {
  name?: string
  description?: string | null
  version?: string | null
}

export interface SkillContentUpdateRequest {
  content: string
}

export interface SkillTextContent {
  content: string
}
