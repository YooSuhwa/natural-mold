import type { Skill, SkillKind } from './types/skill'

export type SkillKindFilter = 'all' | SkillKind
export type SkillStateFilter =
  | 'all'
  | 'needs_credentials'
  | 'needs_rerun'
  | 'evaluation_failed'
  | 'published'
  | 'local_draft'

export const ALL_SKILL_FILTER = 'all'

export const SKILL_STATE_FILTERS: readonly SkillStateFilter[] = [
  'all',
  'needs_credentials',
  'needs_rerun',
  'evaluation_failed',
  'published',
  'local_draft',
]

export type SkillListFilters = {
  readonly kind: SkillKindFilter
  readonly state: SkillStateFilter
  readonly query: string
}

export function filterSkillList(
  skills: readonly Skill[],
  filters: SkillListFilters,
): readonly Skill[] {
  return skills.filter(
    (skill) =>
      skillMatchesKind(skill, filters.kind) &&
      skillMatchesStateFilter(skill, filters.state) &&
      skillMatchesSearch(skill, filters.query),
  )
}

export function skillMatchesKind(skill: Skill, kind: SkillKindFilter): boolean {
  return kind === ALL_SKILL_FILTER || skill.kind === kind
}

export function skillMatchesStateFilter(skill: Skill, state: SkillStateFilter): boolean {
  switch (state) {
    case 'all':
      return true
    case 'needs_credentials':
      return skill.health?.state === 'needs_credentials'
    case 'needs_rerun':
      return (
        skill.health?.state === 'needs_rerun' || skill.latest_evaluation_summary?.status === 'stale'
      )
    case 'evaluation_failed':
      return (
        skill.health?.state === 'evaluation_failed' ||
        skill.latest_evaluation_summary?.status === 'failed'
      )
    case 'published':
      return skill.publication_summary?.state?.startsWith('published_') ?? false
    case 'local_draft':
      return (
        !skill.publication_summary?.state ||
        skill.publication_summary.state === 'not_published' ||
        skill.publication_summary.state === 'draft'
      )
  }
}

export function skillMatchesSearch(skill: Skill, query: string): boolean {
  if (!query) return true
  return [skill.name, skill.slug, skill.description, skill.version, skill.kind]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(query))
}
