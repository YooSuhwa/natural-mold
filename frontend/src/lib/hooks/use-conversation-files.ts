'use client'

import { useQuery } from '@tanstack/react-query'
import { conversationsApi } from '@/lib/api/conversations'
import { conversationKeys } from '@/lib/hooks/use-conversations'

/**
 * 대화 파일 목록(`GET /api/conversations/{id}/files`)을 가져온다.
 *
 * 우측 레일은 이 결과에서 `source==='attached'`(사용자 첨부)만 사용한다.
 * 생성 산출물은 `useConversationArtifacts` + `chatArtifactsAtom`(스트리밍 LIVE
 * 업데이트)을 그대로 쓰므로, 이 훅이 생성 산출물의 단일 출처가 되어선 안 된다.
 */
export function useConversationFiles(conversationId: string | null | undefined) {
  return useQuery({
    queryKey: conversationKeys.files(conversationId),
    queryFn: () => conversationsApi.files(conversationId ?? ''),
    enabled: Boolean(conversationId),
    staleTime: 15_000,
  })
}
