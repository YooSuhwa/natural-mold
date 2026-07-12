import { apiFetch } from './client'
import type {
  SkillRevisionDetail,
  SkillRevisionFileContent,
  SkillRevisionFilesResponse,
  SkillRevisionSummary,
  SkillRollbackResponse,
} from '@/lib/types/skill-revision'

export const skillRevisionsApi = {
  list: (skillId: string) => apiFetch<SkillRevisionSummary[]>(`/api/skills/${skillId}/revisions`),

  get: (skillId: string, revisionId: string) =>
    apiFetch<SkillRevisionDetail>(`/api/skills/${skillId}/revisions/${revisionId}`),

  listFiles: (skillId: string, revisionId: string) =>
    apiFetch<SkillRevisionFilesResponse>(`/api/skills/${skillId}/revisions/${revisionId}/files`),

  getFileContent: (skillId: string, revisionId: string, path: string) =>
    apiFetch<SkillRevisionFileContent>(
      `/api/skills/${skillId}/revisions/${revisionId}/files/content?path=${encodeURIComponent(path)}`,
    ),

  rollback: (skillId: string, revisionId: string) =>
    apiFetch<SkillRollbackResponse>(`/api/skills/${skillId}/revisions/${revisionId}/rollback`, {
      method: 'POST',
    }),
}
