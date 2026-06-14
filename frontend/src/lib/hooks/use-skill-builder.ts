'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { skillBuilderApi } from '@/lib/api/skill-builder'
import { requireQueryId } from './query-id'
import type { SkillBuilderStartRequest, SkillDraftPackage } from '@/lib/types/skill-builder'

export const skillBuilderKeys = {
  all: ['skill-builder'] as const,
  detail: (sessionId: string | null | undefined) => ['skill-builder', sessionId] as const,
}

export function useSkillBuilderSession(sessionId: string | null | undefined) {
  return useQuery({
    queryKey: skillBuilderKeys.detail(sessionId),
    queryFn: () => skillBuilderApi.get(requireQueryId(sessionId, 'sessionId')),
    enabled: !!sessionId,
  })
}

export function useStartSkillBuilder() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: SkillBuilderStartRequest) => skillBuilderApi.start(data),
    onSuccess: (session) => {
      qc.setQueryData(skillBuilderKeys.detail(session.id), session)
    },
  })
}

export function useValidateSkillBuilderSession(sessionId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (draft: SkillDraftPackage) => skillBuilderApi.validate(sessionId, draft),
    onSuccess: (session) => {
      qc.setQueryData(skillBuilderKeys.detail(session.id), session)
    },
  })
}

export function useConfirmSkillBuilderSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (sessionId: string) => skillBuilderApi.confirm(sessionId),
    onSuccess: (skill) => {
      invalidateInstalledSkillCaches(qc, skill.id)
    },
  })
}

function invalidateInstalledSkillCaches(
  qc: ReturnType<typeof useQueryClient>,
  skillId: string,
): void {
  qc.invalidateQueries({ queryKey: ['skills'] })
  qc.invalidateQueries({ queryKey: ['skills', skillId] })
  qc.invalidateQueries({ queryKey: ['skills', skillId, 'files'] })
  qc.invalidateQueries({ queryKey: ['skills', skillId, 'content'] })
}
