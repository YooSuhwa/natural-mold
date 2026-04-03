import { apiFetch, API_BASE, ApiError } from './client'
import type { Skill, SkillCreateRequest, SkillUpdateRequest } from '@/lib/types'

export const skillsApi = {
  list: () => apiFetch<Skill[]>('/api/skills'),
  get: (id: string) => apiFetch<Skill>(`/api/skills/${id}`),
  create: (data: SkillCreateRequest) =>
    apiFetch<Skill>('/api/skills', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: SkillUpdateRequest) =>
    apiFetch<Skill>(`/api/skills/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (id: string) => apiFetch<void>(`/api/skills/${id}`, { method: 'DELETE' }),
  upload: async (file: File): Promise<Skill> => {
    const formData = new FormData()
    formData.append('file', file)
    const res = await fetch(`${API_BASE}/api/skills/upload`, {
      method: 'POST',
      body: formData,
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      const err = body.error ?? {}
      throw new ApiError(
        res.status,
        err.code ?? 'UPLOAD_ERROR',
        err.message ?? body.detail ?? 'Upload failed',
      )
    }
    return res.json()
  },
}
