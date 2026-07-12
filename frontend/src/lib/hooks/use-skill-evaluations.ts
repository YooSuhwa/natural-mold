'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { skillEvaluationsApi } from '@/lib/api/skill-evaluations'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import { requireQueryId } from './query-id'
import type {
  SkillCaseFeedbackUpsert,
  SkillEvaluationRun,
  SkillEvaluationRunCancelRequest,
  SkillEvaluationSetCreate,
} from '@/lib/types/skill-evaluation'

const ACTIVE_EVALUATION_RUN_STATUSES: ReadonlySet<SkillEvaluationRun['status']> = new Set([
  'queued',
  'running',
  'grading',
])

export function skillEvaluationRunsRefetchInterval(
  runs: readonly SkillEvaluationRun[] | undefined,
): 1000 | false {
  if (!runs?.some((run) => ACTIVE_EVALUATION_RUN_STATUSES.has(run.status))) return false
  return 1000
}

export const skillEvaluationKeys = {
  sets: (skillId: string | null | undefined) => ['skills', skillId, 'evaluations'] as const,
  set: (skillId: string | null | undefined, evaluationSetId: string | null | undefined) =>
    ['skills', skillId, 'evaluations', evaluationSetId] as const,
  runs: (skillId: string | null | undefined, evaluationSetId: string | null | undefined) =>
    ['skills', skillId, 'evaluations', evaluationSetId, 'runs'] as const,
  versionStats: (skillId: string | null | undefined) =>
    ['skills', skillId, 'evaluations', 'version-stats'] as const,
  caseFeedback: (
    skillId: string | null | undefined,
    evaluationSetId: string | null | undefined,
    runId: string | null | undefined,
  ) => ['skills', skillId, 'evaluations', evaluationSetId, 'runs', runId, 'case-feedback'] as const,
}

export function useSkillEvaluationSets(skillId: string | null | undefined) {
  return useQuery({
    queryKey: skillEvaluationKeys.sets(skillId),
    queryFn: () => skillEvaluationsApi.listSets(requireQueryId(skillId, 'skillId')),
    enabled: !!skillId,
  })
}

export function useSkillEvaluationSet(
  skillId: string | null | undefined,
  evaluationSetId: string | null | undefined,
) {
  return useQuery({
    queryKey: skillEvaluationKeys.set(skillId, evaluationSetId),
    queryFn: () =>
      skillEvaluationsApi.getSet(
        requireQueryId(skillId, 'skillId'),
        requireQueryId(evaluationSetId, 'evaluationSetId'),
      ),
    enabled: !!skillId && !!evaluationSetId,
  })
}

export function useSkillEvaluationRuns(
  skillId: string | null | undefined,
  evaluationSetId: string | null | undefined,
) {
  return useQuery({
    queryKey: skillEvaluationKeys.runs(skillId, evaluationSetId),
    queryFn: () =>
      skillEvaluationsApi.listRuns(
        requireQueryId(skillId, 'skillId'),
        requireQueryId(evaluationSetId, 'evaluationSetId'),
      ),
    enabled: !!skillId && !!evaluationSetId,
    refetchInterval: (query) => skillEvaluationRunsRefetchInterval(query.state.data),
    refetchOnWindowFocus: false,
  })
}

export function useCreateSkillEvaluationSet(skillId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: SkillEvaluationSetCreate) => skillEvaluationsApi.createSet(skillId, data),
    onSuccess: () => {
      invalidateSkillEvaluationCaches(qc, skillId)
    },
  })
}

export function useEstimateSkillEvaluationRun(skillId: string, evaluationSetId: string) {
  return useMutation({
    mutationFn: () => skillEvaluationsApi.estimateRun(skillId, evaluationSetId),
  })
}

export function useCreateSkillEvaluationRun(skillId: string, evaluationSetId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => skillEvaluationsApi.createRun(skillId, evaluationSetId),
    onSuccess: () => {
      invalidateSkillEvaluationCaches(qc, skillId, evaluationSetId)
    },
  })
}

export function useCancelSkillEvaluationRun(skillId: string, evaluationSetId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      runId,
      data,
    }: {
      readonly runId: string
      readonly data: SkillEvaluationRunCancelRequest
    }) => skillEvaluationsApi.cancelRun(skillId, evaluationSetId, runId, data),
    onSuccess: () => {
      invalidateSkillEvaluationCaches(qc, skillId, evaluationSetId)
    },
  })
}

export function useSkillEvaluationVersionStats(skillId: string | null | undefined) {
  return useQuery({
    queryKey: skillEvaluationKeys.versionStats(skillId),
    queryFn: () => skillEvaluationsApi.versionStats(requireQueryId(skillId, 'skillId')),
    enabled: !!skillId,
    staleTime: 30_000,
  })
}

export function useSkillEvaluationCaseFeedback(
  skillId: string | null | undefined,
  evaluationSetId: string | null | undefined,
  runId: string | null | undefined,
) {
  return useQuery({
    queryKey: skillEvaluationKeys.caseFeedback(skillId, evaluationSetId, runId),
    queryFn: () =>
      skillEvaluationsApi.listCaseFeedback(
        requireQueryId(skillId, 'skillId'),
        requireQueryId(evaluationSetId, 'evaluationSetId'),
        requireQueryId(runId, 'runId'),
      ),
    enabled: !!skillId && !!evaluationSetId && !!runId,
  })
}

export function useUpsertSkillCaseFeedback(
  skillId: string,
  evaluationSetId: string,
  runId: string,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: SkillCaseFeedbackUpsert) =>
      skillEvaluationsApi.upsertCaseFeedback(skillId, evaluationSetId, runId, data),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: skillEvaluationKeys.caseFeedback(skillId, evaluationSetId, runId),
      })
    },
  })
}

export function useDeleteSkillCaseFeedback(
  skillId: string,
  evaluationSetId: string,
  runId: string,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (caseIndex: number) =>
      skillEvaluationsApi.deleteCaseFeedback(skillId, evaluationSetId, runId, caseIndex),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: skillEvaluationKeys.caseFeedback(skillId, evaluationSetId, runId),
      })
    },
  })
}

function invalidateSkillEvaluationCaches(
  qc: ReturnType<typeof useQueryClient>,
  skillId: string,
  evaluationSetId?: string,
): void {
  qc.invalidateQueries({ queryKey: skillQueryKeys.all })
  qc.invalidateQueries({ queryKey: skillQueryKeys.detail(skillId) })
  qc.invalidateQueries({ queryKey: skillEvaluationKeys.sets(skillId) })
  qc.invalidateQueries({ queryKey: skillEvaluationKeys.versionStats(skillId) })
  if (evaluationSetId) {
    qc.invalidateQueries({ queryKey: skillEvaluationKeys.set(skillId, evaluationSetId) })
    qc.invalidateQueries({ queryKey: skillEvaluationKeys.runs(skillId, evaluationSetId) })
  }
}
