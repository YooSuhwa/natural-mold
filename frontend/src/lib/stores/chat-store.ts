import { atom } from 'jotai'

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

/** SSE stream 재연결 인디케이터 상태. ``reconnecting`` 동안만 배지 노출.
 *  실패 시 toast 발행 후 idle 로 즉시 복귀 (영구 배지 없음). */
export type ReconnectState = 'idle' | 'reconnecting'

export const reconnectStateAtom = atom<ReconnectState>('idle')
