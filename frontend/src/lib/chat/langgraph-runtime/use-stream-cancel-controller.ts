'use client'

import { useCallback, useEffect, useRef } from 'react'
import type { UseStreamReturn } from '@langchain/react'
import type { PendingNewSubmitState } from './use-submit-checkpoint-controller'
import type { ThreadRunNotice } from './stream-thread-state-projection'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { reportRuntimeFailure } from './runtime-warning'

interface CancelReconciliationActions {
  readonly lifetime: object
  readonly lifetimeRef: { readonly current: object }
  readonly setThreadRunNotice: (notice: ThreadRunNotice | null) => void
  readonly clearPendingEdit: (conversationId: string, attemptId?: number) => void
  readonly clearPendingReload: (conversationId: string, attemptId?: number) => void
  readonly cancelPostRunHydration: () => void
  readonly getPendingEditAttemptId: () => number | null
  readonly getPendingReloadAttemptId: () => number | null
}

interface UseStreamCancelControllerOptions<StateType extends object> {
  readonly conversationId: string
  readonly stream: UseStreamReturn<StateType>
  readonly reconciliation: CancelReconciliationActions
  readonly pendingSubmit: PendingNewSubmitState | null
  readonly clearPendingSubmit: (content: string, attemptId: number) => boolean
  readonly setChatCancelInFlight: (inFlight: boolean) => void
}

export function useStreamCancelController<StateType extends object>({
  conversationId,
  stream,
  reconciliation,
  pendingSubmit,
  clearPendingSubmit,
  setChatCancelInFlight,
}: UseStreamCancelControllerOptions<StateType>) {
  const nextCancelAttemptRef = useRef(1)
  const activeCancelRef = useRef<{ conversationId: string; attemptId: number } | null>(null)

  useEffect(() => {
    const activeCancel = activeCancelRef.current
    if (activeCancel && activeCancel.conversationId !== conversationId) {
      activeCancelRef.current = null
      setChatCancelInFlight(false)
    }
  }, [conversationId, setChatCancelInFlight])

  return useCallback(async (): Promise<void> => {
    const lifetime = reconciliation.lifetime
    const owner = { conversationId, attemptId: nextCancelAttemptRef.current++ }
    const editAttemptId = reconciliation.getPendingEditAttemptId()
    const reloadAttemptId = reconciliation.getPendingReloadAttemptId()
    activeCancelRef.current = owner
    reconciliation.cancelPostRunHydration()
    setChatCancelInFlight(true)
    try {
      await stream.stop()
      let activeRun: Awaited<ReturnType<typeof conversationRunsApi.active>> | null = null
      try {
        activeRun = await conversationRunsApi.active(conversationId)
      } catch (caught) {
        reportRuntimeFailure(caught, 'cancel_active_lookup_failed')
      }
      if (activeRun?.status === 'queued' || activeRun?.status === 'running') {
        await conversationRunsApi.cancel(conversationId, activeRun.id)
      }
      if (reconciliation.lifetimeRef.current === lifetime) {
        reconciliation.setThreadRunNotice({ id: `local-${conversationId}`, status: 'canceled' })
      }
    } finally {
      if (editAttemptId !== null) reconciliation.clearPendingEdit(conversationId, editAttemptId)
      if (reloadAttemptId !== null) {
        reconciliation.clearPendingReload(conversationId, reloadAttemptId)
      }
      if (pendingSubmit?.attemptId !== undefined) {
        clearPendingSubmit(pendingSubmit.content, pendingSubmit.attemptId)
      }
      if (activeCancelRef.current === owner) {
        activeCancelRef.current = null
        setChatCancelInFlight(false)
      }
    }
  }, [
    clearPendingSubmit,
    conversationId,
    pendingSubmit,
    reconciliation,
    setChatCancelInFlight,
    stream,
  ])
}
