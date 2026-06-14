'use client'

export const MOLDY_BRANCH_SWITCHED_EVENT = 'moldy:branch-switched'

export interface MoldyBranchSwitchedDetail {
  readonly conversationId: string
  readonly checkpointId: string
}

export function dispatchMoldyBranchSwitched(detail: MoldyBranchSwitchedDetail): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(MOLDY_BRANCH_SWITCHED_EVENT, { detail }))
}

export function isMoldyBranchSwitchedEvent(
  event: Event,
): event is CustomEvent<MoldyBranchSwitchedDetail> {
  if (!(event instanceof CustomEvent)) return false
  const detail = event.detail
  return (
    isRecord(detail) &&
    typeof detail.conversationId === 'string' &&
    typeof detail.checkpointId === 'string'
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
