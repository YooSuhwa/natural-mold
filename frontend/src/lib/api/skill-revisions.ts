import { apiFetch } from './client'
import type {
  SkillRevisionDetail,
  SkillRevisionSummary,
  SkillRollbackResponse,
} from '@/lib/types/skill-revision'

export const skillRevisionsApi = {
  list: (skillId: string) => apiFetch<SkillRevisionSummary[]>(`/api/skills/${skillId}/revisions`),

  get: (skillId: string, revisionId: string) =>
    apiFetch<SkillRevisionDetail>(`/api/skills/${skillId}/revisions/${revisionId}`),

  rollback: (skillId: string, revisionId: string) =>
    apiFetch<SkillRollbackResponse>(`/api/skills/${skillId}/revisions/${revisionId}/rollback`, {
      method: 'POST',
    }),
}
