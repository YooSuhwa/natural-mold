import { apiFetch } from './client'
import type { Skill } from '@/lib/types/skill'
import type {
  SkillBuilderFileContent,
  SkillBuilderFilesResponse,
  SkillBuilderSession,
  SkillBuilderSessionBrief,
  SkillBuilderSessionListParams,
  SkillBuilderStartRequest,
  SkillDraftPackage,
} from '@/lib/types/skill-builder'

export const skillBuilderApi = {
  start: (data: SkillBuilderStartRequest) =>
    apiFetch<SkillBuilderSession>('/api/skill-builder', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  list: (params?: SkillBuilderSessionListParams) => {
    const query = new URLSearchParams()
    if (params?.skill_id) query.set('skill_id', params.skill_id)
    if (params?.status) query.set('status', params.status)
    if (params?.limit) query.set('limit', String(params.limit))
    const suffix = query.size > 0 ? `?${query.toString()}` : ''
    return apiFetch<SkillBuilderSessionBrief[]>(`/api/skill-builder${suffix}`)
  },

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

  files: (sessionId: string) =>
    apiFetch<SkillBuilderFilesResponse>(`/api/skill-builder/${sessionId}/files`),

  fileContent: (sessionId: string, path: string) =>
    apiFetch<SkillBuilderFileContent>(
      `/api/skill-builder/${sessionId}/files/content?path=${encodeURIComponent(path)}`,
    ),
}
