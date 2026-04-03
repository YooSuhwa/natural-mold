import { apiFetch } from './client'
import type { Skill, SkillCreateRequest, SkillUpdateRequest } from '@/lib/types'

export const skillsApi = {
  list: () => apiFetch<Skill[]>('/api/skills'),
  get: (id: string) => apiFetch<Skill>(`/api/skills/${id}`),
  create: (data: SkillCreateRequest) =>
    apiFetch<Skill>('/api/skills', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: SkillUpdateRequest) =>
    apiFetch<Skill>(`/api/skills/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (id: string) => apiFetch<void>(`/api/skills/${id}`, { method: 'DELETE' }),
}
