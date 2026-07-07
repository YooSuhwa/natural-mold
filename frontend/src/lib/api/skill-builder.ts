import { apiFetch } from './client'
import type { Skill } from '@/lib/types/skill'
import type {
  SkillBuilderSession,
  SkillBuilderStartRequest,
  SkillDraftPackage,
} from '@/lib/types/skill-builder'

export const skillBuilderApi = {
  start: (data: SkillBuilderStartRequest) =>
    apiFetch<SkillBuilderSession>('/api/skill-builder', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  get: (sessionId: string) => apiFetch<SkillBuilderSession>(`/api/skill-builder/${sessionId}`),

  validate: (sessionId: string, draft: SkillDraftPackage) =>
    apiFetch<SkillBuilderSession>(`/api/skill-builder/${sessionId}/validate`, {
      method: 'POST',
      body: JSON.stringify(draft),
    }),

  confirm: (sessionId: string) =>
    apiFetch<Skill>(`/api/skill-builder/${sessionId}/confirm`, {
      method: 'POST',
    }),
}
