'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { skillBuilderApi } from '@/lib/api/skill-builder'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import { requireQueryId } from './query-id'
import type {
  SkillBuilderSessionListParams,
  SkillBuilderStartRequest,
  SkillDraftPackage,
} from '@/lib/types/skill-builder'

export const skillBuilderKeys = {
  all: ['skill-builder'] as const,
  lists: ['skill-builder', 'list'] as const,
  list: (params?: SkillBuilderSessionListParams) =>
    ['skill-builder', 'list', params ?? {}] as const,
  detail: (sessionId: string | null | undefined) => ['skill-builder', sessionId] as const,
  files: (sessionId: string | null | undefined) => ['skill-builder', sessionId, 'files'] as const,
  fileContent: (sessionId: string | null | undefined, path: string | null) =>
    ['skill-builder', sessionId, 'files', path] as const,
}

export function useSkillBuilderSessions(params?: SkillBuilderSessionListParams) {
  return useQuery({
    queryKey: skillBuilderKeys.list(params),
    queryFn: () => skillBuilderApi.list(params),
    staleTime: 15_000,
  })
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
      // 빌더 인덱스 목록(staleTime 15s)에 새 세션이 즉시 반영되게 한다.
      qc.invalidateQueries({ queryKey: skillBuilderKeys.lists })
    },
  })
}

/**
 * 빌더 세션 시작 + 라우팅 + 실패 토스트의 단일 정본 — 진입점 3곳(목록 행
 * "수정", 컨텍스트 바 "대화로 개선", 빌더 인덱스/생성 다이얼로그)이 공유한다.
 * 성공 시 세션 라우트로 이동하고 true를 반환한다.
 */
export function useBuilderSessionLauncher() {
  const router = useRouter()
  const t = useTranslations('skill.builderChat')
  const startBuilder = useStartSkillBuilder()

  async function launch(payload: SkillBuilderStartRequest): Promise<boolean> {
    try {
      const session = await startBuilder.mutateAsync(payload)
      router.push(`/skills/builder/${session.id}`)
      return true
    } catch {
      toast.error(t('startFailed'))
      return false
    }
  }

  return {
    pending: startBuilder.isPending,
    startCreate: (request: string) => launch({ mode: 'create', user_request: request }),
    startImprove: (skillId: string) =>
      launch({
        mode: 'improve',
        user_request: t('improveDefaultRequest'),
        source_skill_id: skillId,
      }),
  }
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
  // finalize/confirm은 세션 상태(completed)도 바꾼다 — 인덱스 목록 배지 동기화.
  qc.invalidateQueries({ queryKey: skillBuilderKeys.lists })
}
