'use client'

import { useEffect, useRef } from 'react'
import { useAtomValue, useSetAtom } from 'jotai'
import { useAuiState } from '@assistant-ui/react'
import { conversationsApi } from '@/lib/api/conversations'
import { followupEnabledAtom, setConversationFollowupAtom } from '@/lib/stores/chat-followup'
import { reportClientError } from '@/lib/logging/client-logger'

/**
 * 런 종료(thread.isRunning true→false) 시 follow-up 제안 1개를 생성해
 * 대화별 atom에 싣는다 (use-files-run-sync의 완료 감지 패턴).
 *
 * - 토글 OFF면 호출 자체를 하지 않는다(비용 0).
 * - 새 런 시작 시 이전 제안을 비워 낡은 제안이 남지 않게 한다.
 * - 응답이 늦게 도착했는데 대화가 바뀌었으면 버린다(레이스 가드).
 * - 실패는 조용히 무시 — 고스트는 nice-to-have, 채팅을 막지 않는다.
 */
export function useFollowupSuggestion(conversationId: string | null): void {
  const enabled = useAtomValue(followupEnabledAtom)
  const setFollowup = useSetAtom(setConversationFollowupAtom)
  const isRunning = useAuiState((s) => s.thread.isRunning)
  const prevRunning = useRef(isRunning)

  useEffect(() => {
    const wasRunning = prevRunning.current
    prevRunning.current = isRunning
    if (!conversationId) return

    // 런 시작 — 직전 턴의 제안은 더 이상 유효하지 않다.
    if (!wasRunning && isRunning) {
      setFollowup({ conversationId, suggestion: null })
      return
    }

    if (!wasRunning || isRunning || !enabled) return

    let cancelled = false
    conversationsApi
      .followupSuggestion(conversationId)
      .then((response) => {
        if (cancelled) return
        setFollowup({ conversationId, suggestion: response.suggestion ?? null })
      })
      .catch((error) => {
        reportClientError('useFollowupSuggestion', 'suggestion fetch failed:', error)
      })
    return () => {
      cancelled = true
    }
  }, [conversationId, enabled, isRunning, setFollowup])
}
