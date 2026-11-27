'use client'

import { useCallback, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { UseStreamReturn } from '@langchain/react'
import { useSetAtom } from 'jotai'
import { terminalRunNotice, type ThreadRunNotice } from './stream-thread-state-projection'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { useIndependentJobCancelChoice } from '@/components/chat/independent-job-cancel-provider'
import { conversationRunKeys } from '@/lib/hooks/use-conversation-runs'
import { conversationKeys, invalidateConversationNavigators } from '@/lib/hooks/use-conversations'
import { conversationRuntimeStatusAtom } from '@/lib/stores/chat-navigator-store'
import { isActiveRunStatus } from '@/lib/chat-runs/status'
import { reportRuntimeFailure } from './runtime-warning'
import type { ConversationRun, MessagesEnvelope } from '@/lib/types'
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
  readonly acceptPendingSubmit?: (runId: string) => boolean
  readonly setChatCancelInFlight: (inFlight: boolean) => void
}

export function useStreamCancelController<StateType extends object>({
  conversationId,
  stream,
  reconciliation,
  acceptPendingSubmit,
  setChatCancelInFlight,
}: UseStreamCancelControllerOptions<StateType>) {
  const pendingScopeRef = useRef<symbol | null>(null)
  const activeCancelRef = useRef<symbol | null>(null)
  const cancelFollowRef = useRef<{
    readonly conversationId: string
    readonly runId: string
    readonly abort: AbortController
  } | null>(null)
  const queryClient = useQueryClient()
  const chooseCancelScope = useIndependentJobCancelChoice(conversationId)
  const setConversationRuntimeStatus = useSetAtom(conversationRuntimeStatusAtom)

  useEffect(() => {
    return () => {
      pendingScopeRef.current = null
      activeCancelRef.current = null
      cancelFollowRef.current?.abort.abort()
      cancelFollowRef.current = null
      setChatCancelInFlight(false)
    }
  }, [conversationId, setChatCancelInFlight])

  const settleRun = useCallback(
    (run: ConversationRun, expectedRunId: string, lifetime: object): void => {
      if (run.id !== expectedRunId || reconciliation.lifetimeRef.current !== lifetime) return
      if (run.status === 'canceled' || run.status === 'failed' || run.status === 'stale') {
        reconciliation.setThreadRunNotice(terminalRunNotice(run))
      } else if (run.status === 'completed' || run.status === 'interrupted') {
        reconciliation.setThreadRunNotice(null)
      } else {
        return
      }
      setConversationRuntimeStatus((current) => ({ ...current, [conversationId]: 'idle' }))
      const messagesKey = conversationKeys.messages(conversationId)
      void queryClient.cancelQueries({ queryKey: messagesKey, exact: true })
      queryClient.setQueryData<MessagesEnvelope>(messagesKey, (current) =>
        current
          ? {
              ...current,
              active_run: null,
              latest_run: run,
            }
          : current,
      )
      queryClient.invalidateQueries({ queryKey: messagesKey, exact: true })
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
    const owner = Symbol()
    pendingScopeRef.current = owner
    const ownsChoice = (): boolean =>
      pendingScopeRef.current === owner && reconciliation.lifetimeRef.current === lifetime
    let checkedRun: ConversationRun | null | undefined
    let includeIndependentJobs = false
    if (chooseCancelScope) {
      const candidate = await conversationRunsApi.active(conversationId)
      if (!ownsChoice()) return
      checkedRun = candidate
      if (candidate?.status === 'queued' || candidate?.status === 'running') {
        const confirmation = chooseCancelScope(candidate.id)
        if (confirmation) {
          const scope = await confirmation
          if (scope === 'dismiss' || !ownsChoice()) return
          // Keep the stream attached while the dialog is open. A later turn must
          // never receive a stop intended for the run shown in that dialog.
          const current = await conversationRunsApi.active(conversationId)
          if (
            !ownsChoice() ||
            current?.id !== candidate.id ||
            (current.status !== 'queued' && current.status !== 'running')
          )
            return
          checkedRun = current
          includeIndependentJobs = scope === 'cascade'
        }
      }
    }
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
        activeRun =
          checkedRun === undefined ? await conversationRunsApi.active(conversationId) : checkedRun
      } catch (caught) {
        reportRuntimeFailure(caught, 'cancel_active_lookup_failed')
        throw caught
      }
      if (!isCurrentAttempt()) return
      if (activeRun) acceptPendingSubmit?.(activeRun.id)
      if (activeRun?.status === 'queued' || activeRun?.status === 'running') {
        cancelResponse = includeIndependentJobs
          ? await conversationRunsApi.cancel(conversationId, activeRun.id, true)
          : await conversationRunsApi.cancel(conversationId, activeRun.id)
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
      if (activeCancelRef.current === owner) {
        setChatCancelInFlight(false)
      }
    }
  }, [
    conversationId,
    chooseCancelScope,
    acceptPendingSubmit,
    queryClient,
    reconciliation,
    settleRun,
    setConversationRuntimeStatus,
    setChatCancelInFlight,
    stream,
  ])
}
