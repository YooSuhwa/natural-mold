import { apiFetch } from './client'
import type { SkillFeedbackSummary, SkillFeedbackUpsert } from '@/lib/types/skill-feedback'

export const skillFeedbackApi = {
  getSummary: (skillId: string) =>
    apiFetch<SkillFeedbackSummary>(`/api/skills/${skillId}/feedback`),

  upsert: (skillId: string, data: SkillFeedbackUpsert) =>
    apiFetch<SkillFeedbackSummary>(`/api/skills/${skillId}/feedback`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  remove: (skillId: string) =>
    apiFetch<void>(`/api/skills/${skillId}/feedback`, { method: 'DELETE' }),
}
