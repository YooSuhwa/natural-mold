import { apiFetch } from './client'
import type {
  SkillCaseFeedback,
  SkillCaseFeedbackUpsert,
  SkillEvaluationRun,
  SkillEvaluationRunCancelRequest,
  SkillEvaluationRunEstimate,
  SkillEvaluationSet,
  SkillEvaluationSetCreate,
  SkillEvaluationVersionStats,
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

  versionStats: (skillId: string) =>
    apiFetch<SkillEvaluationVersionStats[]>(`/api/skills/${skillId}/evaluations/version-stats`),

  listCaseFeedback: (skillId: string, evaluationSetId: string, runId: string) =>
    apiFetch<SkillCaseFeedback[]>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}/runs/${runId}/case-feedback`,
    ),

  upsertCaseFeedback: (
    skillId: string,
    evaluationSetId: string,
    runId: string,
    data: SkillCaseFeedbackUpsert,
  ) =>
    apiFetch<SkillCaseFeedback>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}/runs/${runId}/case-feedback`,
      {
        method: 'PUT',
        body: JSON.stringify(data),
      },
    ),

  deleteCaseFeedback: (
    skillId: string,
    evaluationSetId: string,
    runId: string,
    caseIndex: number,
  ) =>
    apiFetch<void>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}/runs/${runId}` +
        `/case-feedback/${caseIndex}`,
      { method: 'DELETE' },
    ),
}
