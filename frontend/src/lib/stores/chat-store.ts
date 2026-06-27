import { atom } from 'jotai'
import type { TokenUsageBreakdown } from '@/lib/types'

export interface TokenUsage {
  inputTokens: number
  outputTokens: number
  cost: number
}

export const sessionTokenUsageAtom = atom<TokenUsage>({
  inputTokens: 0,
  outputTokens: 0,
  cost: 0,
})

/**
 * 가장 최근 assistant 턴의 usage breakdown (세션 누적 X). 컨텍스트 창 사용량
 * 게이지가 점유량으로 `prompt_tokens`(= LangChain `input_tokens`, cache 포함 총
 * input)를 읽는다. 세션 누적(`sessionTokenUsageAtom`)은 매 턴 input+output을
 * 모두 더해 컨텍스트 점유량으로는 과대계상이라 별도 atom으로 둔다. 첫 턴 전이거나
 * usage 미발행 모델이면 null.
 */
export const latestTurnUsageAtom = atom<TokenUsageBreakdown | null>(null)

/** SSE stream 재연결 인디케이터 상태. ``reconnecting`` 동안만 배지 노출.
 *  실패 시 toast 발행 후 idle 로 즉시 복귀 (영구 배지 없음). */
export type ReconnectState = 'idle' | 'reconnecting'

export const reconnectStateAtom = atom<ReconnectState>('idle')

/** 서버 cancel 요청이 진행 중인 동안 Stop 버튼 연타를 막기 위한 UI 상태. */
export const chatCancelInFlightAtom = atom(false)

export interface PendingEditBranchPickerSuppression {
  conversationId: string | null
  messageId: string | null
  content: string
}

export const pendingEditBranchPickerSuppressionAtom =
  atom<PendingEditBranchPickerSuppression | null>(null)
