'use client'

import { createContext, useContext } from 'react'

/**
 * Provided by `GroupedApprovalCard` so the child `ApprovalCard` rows can (a) know
 * they render inside a multi-action group — and therefore drop their own header
 * and countdown for a compact layout — and (b) register their approve callback so
 * the group's single "모두 승인" (approve all) button can drive every undecided
 * action at once. The HiTL coordinator still batches the N decisions and resumes
 * once, so this context is purely a UI-coordination layer.
 */
export interface MultiApprovalContextValue {
  /** Register (or replace) this action's asynchronous approve callback. */
  register: (actionIndex: number, approve: () => Promise<boolean>) => void
  /** Remove this action once it is decided or its card unmounts. */
  unregister: (actionIndex: number) => void
  /** Record an action only after its decision has been accepted by the runtime. */
  resolve: (actionIndex: number) => void
}

export const MultiApprovalContext = createContext<MultiApprovalContextValue | null>(null)

export function useMultiApproval(): MultiApprovalContextValue | null {
  return useContext(MultiApprovalContext)
}
