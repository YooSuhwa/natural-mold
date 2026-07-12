'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { skillFeedbackApi } from '@/lib/api/skill-feedback'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import { requireQueryId } from './query-id'
import type { SkillFeedbackUpsert } from '@/lib/types/skill-feedback'

export function useSkillFeedback(skillId: string | null | undefined) {
  return useQuery({
    queryKey: skillQueryKeys.feedback(skillId),
    queryFn: () => skillFeedbackApi.getSummary(requireQueryId(skillId, 'skillId')),
    enabled: !!skillId,
  })
}

export function useUpsertSkillFeedback(skillId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: SkillFeedbackUpsert) => skillFeedbackApi.upsert(skillId, data),
    onSuccess: (summary) => {
      qc.setQueryData(skillQueryKeys.feedback(skillId), summary)
    },
  })
}

export function useDeleteSkillFeedback(skillId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => skillFeedbackApi.remove(skillId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: skillQueryKeys.feedback(skillId) })
    },
  })
}
