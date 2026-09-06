'use client'

import { useCallback, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { UseStreamReturn } from '@langchain/react'
import { useSetAtom } from 'jotai'
import type { PendingNewSubmitState } from './use-submit-checkpoint-controller'
import type { ThreadRunNotice } from './stream-thread-state-projection'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { conversationRunKeys } from '@/lib/hooks/use-conversation-runs'
import { conversationKeys, invalidateConversationNavigators } from '@/lib/hooks/use-conversations'
import { conversationRuntimeStatusAtom } from '@/lib/stores/chat-navigator-store'
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
  const queryClient = useQueryClient()
  const setConversationRuntimeStatus = useSetAtom(conversationRuntimeStatusAtom)

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
    let activeRun: Awaited<ReturnType<typeof conversationRunsApi.active>> | null = null
    let canceledRun: Awaited<ReturnType<typeof conversationRunsApi.active>> | null = null
    let activeLookupSucceeded = false
    let cancelSettled = false
    activeCancelRef.current = owner
    reconciliation.cancelPostRunHydration()
    setChatCancelInFlight(true)
    try {
      try {
        void Promise.resolve(stream.stop()).catch((caught: unknown) => {
          reportRuntimeFailure(caught, 'cancel_stream_stop_failed')
        })
      } catch (caught) {
        reportRuntimeFailure(caught, 'cancel_stream_stop_failed')
      }
      try {
        activeRun = await conversationRunsApi.active(conversationId)
        activeLookupSucceeded = true
      } catch (caught) {
        reportRuntimeFailure(caught, 'cancel_active_lookup_failed')
      }
      if (activeRun?.status === 'queued' || activeRun?.status === 'running') {
        canceledRun = await conversationRunsApi.cancel(conversationId, activeRun.id)
      }
      cancelSettled = activeLookupSucceeded
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
        if (cancelSettled && reconciliation.lifetimeRef.current === lifetime) {
          setConversationRuntimeStatus((current) => ({ ...current, [conversationId]: 'idle' }))
          queryClient.invalidateQueries({ queryKey: conversationKeys.messages(conversationId) })
          queryClient.invalidateQueries({ queryKey: conversationRunKeys.active(conversationId) })
          if (activeRun) {
            queryClient.invalidateQueries({
              queryKey: conversationRunKeys.detail(conversationId, activeRun.id),
            })
          }
          invalidateConversationNavigators(
            queryClient,
            canceledRun?.agent_id ?? activeRun?.agent_id,
            conversationId,
          )
        }
        activeCancelRef.current = null
        setChatCancelInFlight(false)
      }
    }
  }, [
    clearPendingSubmit,
    conversationId,
    pendingSubmit,
    queryClient,
    reconciliation,
    setConversationRuntimeStatus,
    setChatCancelInFlight,
    stream,
  ])
}
