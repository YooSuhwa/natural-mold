import { apiFetch } from './client'
import type { SkillUsageSummary } from '@/lib/types/skill-usage'

export const skillUsageApi = {
  getSummary: (skillId: string, days = 30) =>
    apiFetch<SkillUsageSummary>(`/api/skills/${skillId}/usage?days=${days}`),
}
