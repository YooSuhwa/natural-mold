'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { skillBuilderApi } from '@/lib/api/skill-builder'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import { requireQueryId } from './query-id'
import type { SkillBuilderStartRequest, SkillDraftPackage } from '@/lib/types/skill-builder'

export const skillBuilderKeys = {
  all: ['skill-builder'] as const,
  detail: (sessionId: string | null | undefined) => ['skill-builder', sessionId] as const,
  files: (sessionId: string | null | undefined) => ['skill-builder', sessionId, 'files'] as const,
  fileContent: (sessionId: string | null | undefined, path: string | null) =>
    ['skill-builder', sessionId, 'files', path] as const,
}

export function useSkillBuilderSession(sessionId: string | null | undefined) {
  return useQuery({
    queryKey: skillBuilderKeys.detail(sessionId),
    queryFn: () => skillBuilderApi.get(requireQueryId(sessionId, 'sessionId')),
    enabled: !!sessionId,
  })
}

export function useSkillBuilderFiles(sessionId: string | null | undefined) {
  return useQuery({
    queryKey: skillBuilderKeys.files(sessionId),
    queryFn: () => skillBuilderApi.files(requireQueryId(sessionId, 'sessionId')),
    enabled: !!sessionId,
  })
}

export function useSkillBuilderFileContent(
  sessionId: string | null | undefined,
  path: string | null,
) {
  return useQuery({
    queryKey: skillBuilderKeys.fileContent(sessionId, path),
    queryFn: () => skillBuilderApi.fileContent(requireQueryId(sessionId, 'sessionId'), path ?? ''),
    enabled: !!sessionId && !!path,
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
  qc.invalidateQueries({ queryKey: skillQueryKeys.all })
  qc.invalidateQueries({ queryKey: skillQueryKeys.detail(skillId) })
  qc.invalidateQueries({ queryKey: skillQueryKeys.files(skillId) })
  qc.invalidateQueries({ queryKey: skillQueryKeys.content(skillId) })
}
