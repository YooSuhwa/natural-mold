'use client'

import { useEffect, useRef } from 'react'
import { useAui, useAuiState } from '@assistant-ui/react'

import { reportClientWarning } from '@/lib/logging/client-logger'
import type { MessagesEnvelope } from '@/lib/types'
import type { SkillBuilderSession } from '@/lib/types/skill-builder'

/**
 * 자동 첫 메시지 발화 가능 여부 — 서버 진실 기반 가드 (순수 함수, Phase 1.5).
 *
 * create/improve 다이얼로그의 user_request는 세션 생성 시 저장만 되고 전송되지
 * 않아 사용자가 빌더 화면에서 같은 요청을 다시 입력해야 했다. 아래 조건이 모두
 * 참일 때만 자동 전송한다:
 *
 * - 세션이 v2 활성 상태(`active`) — completed/confirming 세션 재진입 제외
 * - envelope 쿼리가 해결됨 — 로딩 중 판단 금지
 * - 대화 이력 0건 + run 이력 없음(active/latest 모두) — 리로드·중단 런 재전송 방지
 */
export function resolveAutoFirstMessage(
  session: Pick<SkillBuilderSession, 'status' | 'user_request'> | undefined,
  envelope: MessagesEnvelope | undefined,
): string | null {
  if (!session || session.status !== 'active') return null
  if (!envelope) return null
  if ((envelope.messages?.length ?? 0) > 0) return null
  if (envelope.active_run || envelope.latest_run) return null
  const text = session.user_request?.trim()
  return text ? text : null
}

/**
 * user_request를 빌더 첫 진입 시 자동으로 첫 사용자 메시지로 전송한다.
 *
 * AssistantThread의 composerHint 슬롯으로 렌더되어 AssistantRuntimeProvider
 * 컨텍스트 안에서 thread append가 가능하다 (SkillBuilderTryHint 선례).
 *
 * 재전송 가드 3중: ① 부모가 서버 진실(resolveAutoFirstMessage)로 text를
 * 내려줄 때만, ② 라이브 thread가 비어있지 않으면 no-op(remount 이중 발화
 * 방어), ③ ref latch(StrictMode 이중 effect 방어).
 */
export function SkillBuilderAutoRequest({ text }: { readonly text: string | null }) {
  const aui = useAui()
  // 부분 mock state 방어 — thread 상태를 모르면 발화하지 않는다 (fail-closed).
  const isThreadEmpty = useAuiState((s) => s.thread?.isEmpty ?? false)
  const sentRef = useRef(false)

  useEffect(() => {
    if (!text || sentRef.current || !isThreadEmpty) return
    sentRef.current = true
    try {
      // clarifying-question-ui와 동일 패턴 — thread에 직접 user message append.
      aui.thread().append({ content: [{ type: 'text', text }] })
    } catch (err) {
      reportClientWarning('skill-builder', 'auto first message append error:', err)
    }
  }, [aui, isThreadEmpty, text])

  return null
}
