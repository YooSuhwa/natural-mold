'use client'

import { createContext, useContext } from 'react'
import type { Decision } from '@/lib/types'

export interface HiTLContextValue {
  /**
   * `decisions` 배열 길이는 interrupt의 `action_requests.length`와 일치해야 한다
   * (LangChain `HITLResponse` 계약).
   */
  onResumeDecisions: (decisions: Decision[], displayText?: string) => Promise<void>
  registerDecision?: (
    actionIndex: number,
    decision: Decision,
    displayText?: string,
    interruptId?: string | null,
  ) => Promise<void>
}

export const HiTLContext = createContext<HiTLContextValue | null>(null)

/** Tool UI 컴포넌트에서 HiTL resume을 호출하기 위한 훅 */
export function useHiTL() {
  return useContext(HiTLContext)
}
