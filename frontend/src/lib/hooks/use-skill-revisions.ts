'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { skillRevisionsApi } from '@/lib/api/skill-revisions'
import { requireQueryId } from './query-id'
import { skillEvaluationKeys } from './use-skill-evaluations'

export const skillRevisionKeys = {
  list: (skillId: string | null | undefined) => ['skills', skillId, 'revisions'] as const,
  detail: (skillId: string | null | undefined, revisionId: string | null | undefined) =>
    ['skills', skillId, 'revisions', revisionId] as const,
}

export function useSkillRevisions(skillId: string | null | undefined) {
  return useQuery({
    queryKey: skillRevisionKeys.list(skillId),
    queryFn: () => skillRevisionsApi.list(requireQueryId(skillId, 'skillId')),
    enabled: !!skillId,
  })
}

export function useSkillRevision(
  skillId: string | null | undefined,
  revisionId: string | null | undefined,
) {
  return useQuery({
    queryKey: skillRevisionKeys.detail(skillId, revisionId),
    queryFn: () =>
      skillRevisionsApi.get(
        requireQueryId(skillId, 'skillId'),
        requireQueryId(revisionId, 'revisionId'),
      ),
    enabled: !!skillId && !!revisionId,
  })
}

export function useRollbackSkillRevision(skillId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (revisionId: string) => skillRevisionsApi.rollback(skillId, revisionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] })
      qc.invalidateQueries({ queryKey: ['skills', skillId] })
      qc.invalidateQueries({ queryKey: ['skills', skillId, 'files'] })
      qc.invalidateQueries({ queryKey: ['skills', skillId, 'content'] })
      qc.invalidateQueries({ queryKey: skillEvaluationKeys.sets(skillId) })
      qc.invalidateQueries({ queryKey: skillRevisionKeys.list(skillId) })
    },
  })
}
