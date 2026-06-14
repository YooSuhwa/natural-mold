// Skill domain types — mirrors backend `app/schemas/skill.py`.

import type {
  ExecutionProfile,
  InstallationSummary,
  ResourceOriginSummary,
  ResourcePublicationSummary,
} from './marketplace'
import type { SkillHealthSummary, SkillLatestEvaluationSummary } from './skill-evaluation'

export type SkillKind = 'text' | 'package'

export interface SkillCredentialRequirement {
  key: string
  definition_key: string
  required: boolean
  label: string
  description?: string | null
  fields: readonly string[]
  injection: 'env' | 'config'
  scope: 'user' | 'system_dependency' | 'manual'
}

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
  credential_requirements?: readonly SkillCredentialRequirement[] | null
  execution_profile: ExecutionProfile | null
  current_revision_id?: string | null
  latest_evaluation_summary?: SkillLatestEvaluationSummary | null
  health?: SkillHealthSummary | null
  last_modified_at: string
  created_at: string
  updated_at: string
  // ADR-017 Slice A — origin/publication/installation summaries
  origin_summary?: ResourceOriginSummary | null
  publication_summary?: ResourcePublicationSummary | null
  installation?: InstallationSummary | null
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
