import { API_BASE, apiFetch, apiUpload } from './client'
import type {
  Skill,
  SkillContentUpdateRequest,
  SkillCreateRequest,
  SkillFileEntry,
  SkillKind,
  SkillMetadataUpdateRequest,
  SkillTextContent,
} from '@/lib/types/skill'

export const skillsApi = {
  list: (params?: { kind?: SkillKind; q?: string }) => {
    const search = new URLSearchParams()
    if (params?.kind) search.set('kind', params.kind)
    if (params?.q) search.set('q', params.q)
    const qs = search.toString()
    return apiFetch<Skill[]>(`/api/skills${qs ? `?${qs}` : ''}`)
  },
  get: (id: string) => apiFetch<Skill>(`/api/skills/${id}`),
  createText: (data: SkillCreateRequest) =>
    apiFetch<Skill>('/api/skills', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  patchMetadata: (id: string, data: SkillMetadataUpdateRequest) =>
    apiFetch<Skill>(`/api/skills/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  putContent: (id: string, data: SkillContentUpdateRequest) =>
    apiFetch<Skill>(`/api/skills/${id}/content`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  getContent: (id: string) => apiFetch<SkillTextContent>(`/api/skills/${id}/content`),
  delete: (id: string) => apiFetch<void>(`/api/skills/${id}`, { method: 'DELETE' }),

  listFiles: (id: string) => apiFetch<SkillFileEntry[]>(`/api/skills/${id}/files`),
  fileUrl: (id: string, path: string) => `${API_BASE}/api/skills/${id}/files/${encodeURI(path)}`,

  setFile: (id: string, path: string, content: string) =>
    apiFetch<Skill>(`/api/skills/${id}/files/${encodeURI(path)}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),

  deleteFile: (id: string, path: string) =>
    apiFetch<Skill>(`/api/skills/${id}/files/${encodeURI(path)}`, {
      method: 'DELETE',
    }),

  uploadFile: (id: string, relPath: string, file: File): Promise<Skill> => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('rel_path', relPath)
    return apiUpload<Skill>(`/api/skills/${id}/files`, formData)
  },

  uploadPackage: (file: File): Promise<Skill> => {
    const formData = new FormData()
    formData.append('file', file)
    return apiUpload<Skill>('/api/skills/upload', formData)
  },
}
