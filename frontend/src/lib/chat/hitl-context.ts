'use client'

import { createContext, useContext } from 'react'
import type { Decision } from '@/lib/types'

export interface HiTLContextValue {
  /**
   * 표준 (Phase 2 신규) — LangChain `HITLResponse.decisions[]` 송신 → /resume.
   * `decisions` 배열 길이는 interrupt의 `action_requests.length`와 같아야 한다.
   * (단일 진실 공급원: `docs/exec-plans/active/hitl-phase2-contract.md` §6.3)
   */
  onResumeDecisions: (decisions: Decision[], displayText?: string) => Promise<void>
  /**
   * Legacy — 자체 `{response: ...}` 송신 → /resume. backend router가 단일
   * respond decision으로 변환한다. Phase 3에서 제거 예정.
   *
   * @deprecated Phase 3 제거 예정. 신규 호출자는 `onResumeDecisions` 사용.
   */
  onResume: (response: unknown, displayText?: string) => Promise<void>
}

export const HiTLContext = createContext<HiTLContextValue | null>(null)

/** Tool UI 컴포넌트에서 HiTL resume을 호출하기 위한 훅 */
export function useHiTL() {
  return useContext(HiTLContext)
}
