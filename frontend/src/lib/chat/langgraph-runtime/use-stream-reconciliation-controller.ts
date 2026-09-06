'use client'

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useStream } from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
import type { AppendMessage } from '@assistant-ui/react'
import { createMoldyAgentTransport } from './moldy-agent-transport'
import {
  EMPTY_SERVER_MESSAGE_METADATA,
  interruptsFromThreadState,
  messageMetadataFromThreadState,
  messagesFromThreadState,
  terminalFailureIsStaleForCurrentAttempt,
  terminalRunNoticeFromThreadState,
  type ServerMessageMetadataSnapshot,
  type ThreadRunNotice,
} from './stream-thread-state-projection'
import type { LangGraphInterruptLike } from './hitl-interrupts'
import { useStreamHydrationEffects } from './use-stream-hydration-effects'
import { useReconciliationOperationState } from './use-reconciliation-operation-state'
import { reportRuntimeFailure } from './runtime-warning'
import {
  claimedQueueRunIdAfterTransition,
  followClaimedQueueRunValues,
} from '@/lib/chat/message-queue/follow-claimed-queue-run'

interface MoldyGraphState {
  messages: BaseMessage[]
  todos?: unknown
  files?: unknown
  async_tasks?: unknown
}

interface TaggedValue<T> {
  readonly conversationId: string
  readonly value: T
}

interface ThreadStateHydrationOptions {
  readonly replaceMessages?: boolean
}

interface UseStreamReconciliationControllerOptions {
  readonly agentId: string
  readonly conversationId: string
  readonly onRunStartAccepted?: () => void
  readonly clearBranchPickerSuppression: () => void
}

export function useStreamReconciliationController({
  agentId,
  conversationId,
  onRunStartAccepted,
  clearBranchPickerSuppression,
}: UseStreamReconciliationControllerOptions) {
  const lifetime = useMemo(() => ({ conversationId }), [conversationId])
  const lifetimeRef = useRef(lifetime)
  const onRunStartAcceptedRef = useRef(onRunStartAccepted)
  const acceptedRunIdsRef = useRef(new Set<string>())
  const claimedQueueRunFollowRef = useRef<{
    readonly runId: string
    readonly abort: AbortController
  } | null>(null)
  useLayoutEffect(() => {
    claimedQueueRunFollowRef.current?.abort.abort()
    claimedQueueRunFollowRef.current = null
    lifetimeRef.current = lifetime
    acceptedRunIdsRef.current.clear()
    return () => {
      claimedQueueRunFollowRef.current?.abort.abort()
    }
  }, [lifetime])
  useLayoutEffect(() => {
    onRunStartAcceptedRef.current = onRunStartAccepted
  }, [onRunStartAccepted])

  const [threadRunNoticeState, setThreadRunNoticeState] =
    useState<TaggedValue<ThreadRunNotice | null> | null>(null)
  const [serverMessageMetadataState, setServerMessageMetadataState] =
    useState<TaggedValue<ServerMessageMetadataSnapshot> | null>(null)
  const [serverStateMessages, setServerStateMessages] = useState<TaggedValue<
    readonly BaseMessage[]
  > | null>(null)
  const [serverInterruptsState, setServerInterruptsState] = useState<TaggedValue<
    readonly LangGraphInterruptLike[]
  > | null>(null)
  const [claimedQueueRunState, setClaimedQueueRunState] = useState<TaggedValue<string> | null>(null)
  const operations = useReconciliationOperationState(conversationId, clearBranchPickerSuppression)
  const {
    pendingEditState,
    pendingReloadState,
    pendingEditRender,
    pendingReloadRender,
    reloadRunCorrelation,
    reloadRunCorrelationRef,
    latestVisibleMessagesRef,
    beginPendingEdit,
    updatePendingEdit,
    clearPendingEdit,
    beginPendingReload,
    clearPendingReload,
    acceptReloadRun,
    recordLatestVisibleMessages,
    getLatestVisibleMessages,
    stagePendingEditBase,
    getPendingEditBase,
    getPendingEditAttemptId,
    getPendingReloadAttemptId,
  } = operations
  const threadRunNotice =
    threadRunNoticeState?.conversationId === conversationId ? threadRunNoticeState.value : null
  const serverMessageMetadata =
    serverMessageMetadataState?.conversationId === conversationId
      ? serverMessageMetadataState.value
      : EMPTY_SERVER_MESSAGE_METADATA
  const serverMessages =
    serverStateMessages?.conversationId === conversationId ? serverStateMessages.value : null
  const serverInterrupts =
    serverInterruptsState?.conversationId === conversationId ? serverInterruptsState.value : []
  const claimedQueueRunInFlight = claimedQueueRunState?.conversationId === conversationId
  const setThreadRunNotice = useCallback(
    (notice: ThreadRunNotice | null): void => {
      if (lifetimeRef.current !== lifetime) return
      setThreadRunNoticeState({ conversationId, value: notice })
    },
    [conversationId, lifetime],
  )

  const clearServerHydrationState = useCallback((): void => {
    setServerStateMessages((current) =>
      current?.conversationId === conversationId ? null : current,
    )
    setServerMessageMetadataState((current) =>
      current?.conversationId === conversationId ? null : current,
    )
    setServerInterruptsState((current) =>
      current?.conversationId === conversationId ? null : current,
    )
  }, [conversationId])

  const handleThreadState = useCallback(
    (state: unknown, options?: ThreadStateHydrationOptions): void => {
      if (lifetimeRef.current !== lifetime) return
      const terminalRunNotice = terminalRunNoticeFromThreadState(state)
      if (
        terminalFailureIsStaleForCurrentAttempt(
          conversationId,
          terminalRunNotice,
          reloadRunCorrelationRef.current,
        )
      ) {
        return
      }
      const terminalRunFailed = terminalRunNotice?.status === 'failed'
      setThreadRunNoticeState({ conversationId, value: terminalRunNotice })
      setServerMessageMetadataState({
        conversationId,
        value: messageMetadataFromThreadState(state),
      })
      const interrupts = interruptsFromThreadState(state)
      setServerInterruptsState({ conversationId, value: interrupts })
      if (options?.replaceMessages || interrupts.length > 0 || terminalRunFailed) {
        const messages = messagesFromThreadState(state)
        setServerStateMessages(messages ? { conversationId, value: messages } : null)
      }
      if (terminalRunFailed && reloadRunCorrelationRef.current.pendingReload) {
        clearPendingReload(conversationId)
      }
    },
    [clearPendingReload, conversationId, lifetime, reloadRunCorrelationRef],
  )

  const transport = useMemo(
    () => createMoldyAgentTransport(conversationId, agentId),
    [agentId, conversationId],
  )
  const handleRunStartAccepted = useCallback(
    (runId?: string): void => {
      if (runId && acceptedRunIdsRef.current.has(runId)) return
      if (runId) acceptedRunIdsRef.current.add(runId)
      if (runId && lifetimeRef.current === lifetime) {
        acceptReloadRun(runId)
      }
      onRunStartAcceptedRef.current?.()
    },
    [acceptReloadRun, lifetime],
  )
  const submitQueuedInput = useCallback(
    (message: AppendMessage, strategy: 'enqueue' | 'interrupt', requestId: string) =>
      transport.submitQueuedInput(message, strategy, requestId),
    [transport],
  )

  useEffect(() => {
    transport.setStateHydrationListener(handleThreadState)
    return () => transport.setStateHydrationListener(undefined)
  }, [handleThreadState, transport])

  const stream = useStream<MoldyGraphState>({ transport, threadId: conversationId })
  const streamRef = useRef(stream)
  useLayoutEffect(() => {
    streamRef.current = stream
  }, [stream])
  const handleClaimedQueueRun = useCallback(
    (runId?: string): void => {
      if (lifetimeRef.current !== lifetime) return
      handleRunStartAccepted(runId)
      if (!runId) return
      const currentFollow = claimedQueueRunFollowRef.current
      if (currentFollow?.runId === runId && !currentFollow.abort.signal.aborted) return
      const thread = streamRef.current.getThread()
      if (!thread) return
      currentFollow?.abort.abort()
      const abort = new AbortController()
      const follow = { runId, abort }
      claimedQueueRunFollowRef.current = follow
      const isCurrentFollow = (): boolean =>
        lifetimeRef.current === lifetime &&
        claimedQueueRunFollowRef.current === follow &&
        !abort.signal.aborted
      void followClaimedQueueRunValues(
        thread,
        runId,
        (values) => handleThreadState({ values }, { replaceMessages: true }),
        isCurrentFollow,
        (inFlight) => {
          if (lifetimeRef.current !== lifetime) return
          if (inFlight) {
            setClaimedQueueRunState({ conversationId, value: runId })
            return
          }
          setClaimedQueueRunState((current) => {
            if (current?.conversationId !== conversationId) return current
            const nextRunId = claimedQueueRunIdAfterTransition(current.value, runId, false)
            return nextRunId === null ? null : { conversationId, value: nextRunId }
          })
        },
        abort.signal,
      )
        .then(async (outcome) => {
          if (outcome !== 'terminal') return
          if (!isCurrentFollow()) return
          const state = await transport.readState()
          if (!isCurrentFollow()) return
          handleThreadState(state, { replaceMessages: true })
        })
        .catch((caught: unknown) => {
          reportRuntimeFailure(caught, 'queue_claim_projection_failed')
        })
        .finally(() => {
          if (claimedQueueRunFollowRef.current === follow) claimedQueueRunFollowRef.current = null
        })
    },
    [conversationId, handleRunStartAccepted, handleThreadState, lifetime, transport],
  )

  useEffect(() => {
    transport.setRunStartAcceptedListener(handleClaimedQueueRun)
    return () => transport.setRunStartAcceptedListener(undefined)
  }, [handleClaimedQueueRun, transport])

  const activateTransportHydration = useCallback(
    () => transport.activateStateHydration?.(),
    [transport],
  )
  const hydration = useStreamHydrationEffects({
    conversationId,
    activateTransportHydration,
    streamLoading: stream.isLoading,
    pendingEdit: pendingEditState,
    pendingReload: pendingReloadState,
    reloadRunCorrelation,
    handleThreadState,
    clearServerHydrationState,
    clearPendingEdit,
    clearPendingReload,
    latestVisibleMessagesRef,
  })
  const commandActions = useMemo(
    () => ({
      lifetime,
      lifetimeRef,
      setThreadRunNotice,
      clearServerHydrationState,
      beginPendingEdit,
      updatePendingEdit,
      clearPendingEdit,
      beginPendingReload,
      clearPendingReload,
      cancelPostRunHydration: hydration.cancelPostRunHydration,
      getLatestVisibleMessages,
      stagePendingEditBase,
      getPendingEditBase,
      getPendingEditAttemptId,
      getPendingReloadAttemptId,
    }),
    [
      beginPendingEdit,
      beginPendingReload,
      clearPendingEdit,
      clearPendingReload,
      clearServerHydrationState,
      getLatestVisibleMessages,
      getPendingEditAttemptId,
      getPendingEditBase,
      getPendingReloadAttemptId,
      hydration.cancelPostRunHydration,
      lifetime,
      setThreadRunNotice,
      stagePendingEditBase,
      updatePendingEdit,
    ],
  )
  return {
    stream,
    lifetime,
    lifetimeRef,
    threadRunNotice,
    serverMessageMetadata,
    serverMessages,
    serverInterrupts,
    claimedQueueRunInFlight,
    pendingEditRender,
    pendingReloadRender,
    pendingEditAttemptId:
      pendingEditState?.conversationId === conversationId ? pendingEditState.attemptId : null,
    pendingReloadAttemptId:
      pendingReloadState?.conversationId === conversationId ? pendingReloadState.attemptId : null,
    postRunHydrationPending: hydration.postRunHydrationPending,
    setThreadRunNotice,
    clearServerHydrationState,
    beginPendingEdit,
    updatePendingEdit,
    clearPendingEdit,
    beginPendingReload,
    clearPendingReload,
    cancelPostRunHydration: hydration.cancelPostRunHydration,
    settlePostRunHydration: hydration.settlePostRunHydration,
    recordLatestVisibleMessages,
    getLatestVisibleMessages,
    stagePendingEditBase,
    getPendingEditBase,
    getPendingEditAttemptId,
    getPendingReloadAttemptId,
    commandActions,
    submitQueuedInput,
    handleClaimedQueueRun,
  }
}
