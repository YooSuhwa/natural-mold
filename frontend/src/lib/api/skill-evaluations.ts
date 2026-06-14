import { apiFetch } from './client'
import type {
  SkillEvaluationRun,
  SkillEvaluationRunCancelRequest,
  SkillEvaluationRunEstimate,
  SkillEvaluationSet,
  SkillEvaluationSetCreate,
} from '@/lib/types/skill-evaluation'

export const skillEvaluationsApi = {
  listSets: (skillId: string) =>
    apiFetch<SkillEvaluationSet[]>(`/api/skills/${skillId}/evaluations`),

  createSet: (skillId: string, data: SkillEvaluationSetCreate) =>
    apiFetch<SkillEvaluationSet>(`/api/skills/${skillId}/evaluations`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  getSet: (skillId: string, evaluationSetId: string) =>
    apiFetch<SkillEvaluationSet>(`/api/skills/${skillId}/evaluations/${evaluationSetId}`),

  estimateRun: (skillId: string, evaluationSetId: string) =>
    apiFetch<SkillEvaluationRunEstimate>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}/estimate`,
      { method: 'POST' },
    ),

  listRuns: (skillId: string, evaluationSetId: string) =>
    apiFetch<SkillEvaluationRun[]>(`/api/skills/${skillId}/evaluations/${evaluationSetId}/runs`),

  createRun: (skillId: string, evaluationSetId: string) =>
    apiFetch<SkillEvaluationRun>(`/api/skills/${skillId}/evaluations/${evaluationSetId}/runs`, {
      method: 'POST',
    }),

  cancelRun: (
    skillId: string,
    evaluationSetId: string,
    runId: string,
    data: SkillEvaluationRunCancelRequest,
  ) =>
    apiFetch<SkillEvaluationRun>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}/runs/${runId}/cancel`,
      {
        method: 'POST',
        body: JSON.stringify(data),
      },
    ),
}
