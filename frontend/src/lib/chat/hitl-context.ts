'use client'

import { createContext, useContext } from 'react'

export interface HiTLContextValue {
  /** LangGraph interrupt 응답 → /resume 엔드포인트 호출 */
  onResume: (response: unknown, displayText?: string) => Promise<void>
}

export const HiTLContext = createContext<HiTLContextValue | null>(null)

/** Tool UI 컴포넌트에서 HiTL resume을 호출하기 위한 훅 */
export function useHiTL() {
  return useContext(HiTLContext)
}
