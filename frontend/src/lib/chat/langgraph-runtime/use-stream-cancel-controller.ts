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
import { isActiveRunStatus } from '@/lib/chat-runs/status'
import { reportRuntimeFailure } from './runtime-warning'
import type { ConversationRun } from '@/lib/types'
import { followExactRunToTerminal } from './cancel-run-reconciliation'

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
  readonly stream: Pick<UseStreamReturn<StateType>, 'stop'>
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
  const cancelFollowRef = useRef<{
    readonly conversationId: string
    readonly runId: string
    readonly abort: AbortController
  } | null>(null)
  const queryClient = useQueryClient()
  const setConversationRuntimeStatus = useSetAtom(conversationRuntimeStatusAtom)

  useEffect(() => {
    return () => {
      activeCancelRef.current = null
      cancelFollowRef.current?.abort.abort()
      cancelFollowRef.current = null
      setChatCancelInFlight(false)
    }
  }, [conversationId, setChatCancelInFlight])

  const settleRun = useCallback(
    (run: ConversationRun, expectedRunId: string, lifetime: object): void => {
      if (run.id !== expectedRunId || reconciliation.lifetimeRef.current !== lifetime) return
      if (run.status === 'canceled') {
        reconciliation.setThreadRunNotice({ id: run.id, status: 'canceled' })
      } else if (run.status === 'failed') {
        reconciliation.setThreadRunNotice({
          id: run.id,
          status: 'failed',
          ...(run.error_message ? { errorMessage: run.error_message } : {}),
        })
      } else if (run.status === 'stale') {
        reconciliation.setThreadRunNotice({ id: run.id, status: 'stale' })
      } else if (run.status === 'completed' || run.status === 'interrupted') {
        reconciliation.setThreadRunNotice(null)
      } else {
        return
      }
      setConversationRuntimeStatus((current) => ({ ...current, [conversationId]: 'idle' }))
      queryClient.invalidateQueries({ queryKey: conversationKeys.messages(conversationId) })
      queryClient.invalidateQueries({ queryKey: conversationRunKeys.active(conversationId) })
      queryClient.invalidateQueries({
        queryKey: conversationRunKeys.detail(conversationId, expectedRunId),
      })
      invalidateConversationNavigators(queryClient, run.agent_id, conversationId)
    },
    [conversationId, queryClient, reconciliation, setConversationRuntimeStatus],
  )

  return useCallback(async (): Promise<void> => {
    const lifetime = reconciliation.lifetime
    const owner = { conversationId, attemptId: nextCancelAttemptRef.current++ }
    const editAttemptId = reconciliation.getPendingEditAttemptId()
    const reloadAttemptId = reconciliation.getPendingReloadAttemptId()
    let activeRun: Awaited<ReturnType<typeof conversationRunsApi.active>> | null = null
    let cancelResponse: ConversationRun | null = null
    cancelFollowRef.current?.abort.abort()
    cancelFollowRef.current = null
    activeCancelRef.current = owner
    const isCurrentAttempt = (): boolean =>
      activeCancelRef.current === owner && reconciliation.lifetimeRef.current === lifetime
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
      } catch (caught) {
        reportRuntimeFailure(caught, 'cancel_active_lookup_failed')
        throw caught
      }
      if (!isCurrentAttempt()) return
      if (activeRun?.status === 'queued' || activeRun?.status === 'running') {
        cancelResponse = await conversationRunsApi.cancel(conversationId, activeRun.id)
      }
      if (!isCurrentAttempt()) return
      if (!activeRun) {
        reconciliation.setThreadRunNotice(null)
        setConversationRuntimeStatus((current) => ({ ...current, [conversationId]: 'idle' }))
        queryClient.invalidateQueries({ queryKey: conversationKeys.messages(conversationId) })
        queryClient.invalidateQueries({ queryKey: conversationRunKeys.active(conversationId) })
        invalidateConversationNavigators(queryClient, null, conversationId)
        return
      }
      const exactResponse = cancelResponse?.id === activeRun.id ? cancelResponse : null
      if (exactResponse && !isActiveRunStatus(exactResponse.status)) {
        settleRun(exactResponse, activeRun.id, lifetime)
        return
      }
      reconciliation.setThreadRunNotice({ id: activeRun.id, status: 'canceling' })
      const activeRunId = activeRun.id
      const follow = {
        conversationId,
        runId: activeRunId,
        abort: new AbortController(),
      }
      cancelFollowRef.current = follow
      void followExactRunToTerminal({
        runId: activeRunId,
        signal: follow.abort.signal,
        readRun: (runId, signal) => conversationRunsApi.get(conversationId, runId, signal),
        onTerminal: (run) => {
          if (cancelFollowRef.current !== follow || !isCurrentAttempt()) return
          settleRun(run, activeRunId, lifetime)
        },
        onRecoverableError: (error) => {
          reportRuntimeFailure(error, 'cancel_terminal_follow_failed')
        },
      })
        .catch((error: unknown) => {
          reportRuntimeFailure(error, 'cancel_terminal_follow_failed')
        })
        .finally(() => {
          if (cancelFollowRef.current === follow) cancelFollowRef.current = null
        })
    } finally {
      if (editAttemptId !== null) reconciliation.clearPendingEdit(conversationId, editAttemptId)
      if (reloadAttemptId !== null) {
        reconciliation.clearPendingReload(conversationId, reloadAttemptId)
      }
      if (pendingSubmit?.attemptId !== undefined) {
        clearPendingSubmit(pendingSubmit.content, pendingSubmit.attemptId)
      }
      if (activeCancelRef.current === owner) {
        setChatCancelInFlight(false)
      }
    }
  }, [
    clearPendingSubmit,
    conversationId,
    pendingSubmit,
    queryClient,
    reconciliation,
    settleRun,
    setConversationRuntimeStatus,
    setChatCancelInFlight,
    stream,
  ])
}
