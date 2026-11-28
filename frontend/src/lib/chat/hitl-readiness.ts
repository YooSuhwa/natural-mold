import { useState } from 'react'
import type { HiTLContextValue } from './hitl-context'

export function canRespondToAction(hitl: HiTLContextValue | null, args: unknown): boolean {
  if (hitl?.canRespond === undefined && !hitl?.canRespondTo) return true
  if (!hitl?.registerDecision || hitl.canRespond === false || !args || typeof args !== 'object')
    return false
  if (!('hitl_interrupt_id' in args) || !('hitl_action_index' in args)) return false
  const id = args.hitl_interrupt_id
  const index = args.hitl_action_index
  if (
    typeof id !== 'string' ||
    !id.trim() ||
    typeof index !== 'number' ||
    !Number.isInteger(index) ||
    index < 0
  )
    return false
  return hitl?.canRespondTo?.(id, index) ?? true
}

/** Keep a submitted answer visible while the next committed checkpoint is being read. */
export function useApprovalGeneration(hitl: HiTLContextValue | null): string | null {
  const verified = hitl?.approvalGeneration ?? null
  const [last, setLast] = useState(verified)
  if (verified !== null && verified !== last) setLast(verified)
  return verified ?? last
}
