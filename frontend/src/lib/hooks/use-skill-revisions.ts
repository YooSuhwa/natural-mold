'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { skillRevisionsApi } from '@/lib/api/skill-revisions'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import { requireQueryId } from './query-id'
import { skillEvaluationKeys } from './use-skill-evaluations'

export const skillRevisionKeys = {
  list: (skillId: string | null | undefined) => ['skills', skillId, 'revisions'] as const,
  detail: (skillId: string | null | undefined, revisionId: string | null | undefined) =>
    ['skills', skillId, 'revisions', revisionId] as const,
  files: (skillId: string | null | undefined, revisionId: string | null | undefined) =>
    ['skills', skillId, 'revisions', revisionId, 'files'] as const,
  fileContent: (
    skillId: string | null | undefined,
    revisionId: string | null | undefined,
    path: string | null,
  ) => ['skills', skillId, 'revisions', revisionId, 'files', path] as const,
}

export function useSkillRevisionFiles(
  skillId: string | null | undefined,
  revisionId: string | null | undefined,
) {
  return useQuery({
    queryKey: skillRevisionKeys.files(skillId, revisionId),
    queryFn: () =>
      skillRevisionsApi.listFiles(
        requireQueryId(skillId, 'skillId'),
        requireQueryId(revisionId, 'revisionId'),
      ),
    enabled: !!skillId && !!revisionId,
    // 스냅샷은 불변 — 재조회 불필요.
    staleTime: Infinity,
  })
}

export function useSkillRevisionFileContent(
  skillId: string | null | undefined,
  revisionId: string | null | undefined,
  path: string | null,
) {
  return useQuery({
    queryKey: skillRevisionKeys.fileContent(skillId, revisionId, path),
    queryFn: () =>
      skillRevisionsApi.getFileContent(
        requireQueryId(skillId, 'skillId'),
        requireQueryId(revisionId, 'revisionId'),
        path ?? '',
      ),
    enabled: !!skillId && !!revisionId && !!path,
    staleTime: Infinity,
    // 바이너리/pruned/미존재는 404 계약(fail-closed) — 재시도 없이 placeholder.
    retry: false,
  })
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
      qc.invalidateQueries({ queryKey: skillQueryKeys.all })
      qc.invalidateQueries({ queryKey: skillQueryKeys.detail(skillId) })
      qc.invalidateQueries({ queryKey: skillQueryKeys.files(skillId) })
      qc.invalidateQueries({ queryKey: skillQueryKeys.content(skillId) })
      qc.invalidateQueries({ queryKey: skillEvaluationKeys.sets(skillId) })
      qc.invalidateQueries({ queryKey: skillRevisionKeys.list(skillId) })
    },
  })
}
