import type { SkillKind } from '@/lib/types/skill'

export type SkillListQueryParams = {
  readonly kind?: SkillKind
  readonly q?: string
}

export const skillQueryKeys = {
  all: ['skills'] as const,
  list: (params?: SkillListQueryParams) => ['skills', params ?? {}] as const,
  detail: (skillId: string | null | undefined) => ['skills', skillId] as const,
  files: (skillId: string | null | undefined) => ['skills', skillId, 'files'] as const,
  content: (skillId: string | null | undefined) => ['skills', skillId, 'content'] as const,
}
