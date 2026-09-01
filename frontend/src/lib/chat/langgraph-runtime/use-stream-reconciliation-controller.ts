'use client'

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useStream } from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
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
  useLayoutEffect(() => {
    lifetimeRef.current = lifetime
  }, [lifetime])

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
      if (runId && lifetimeRef.current === lifetime) {
        acceptReloadRun(runId)
      }
      onRunStartAccepted?.()
    },
    [acceptReloadRun, lifetime, onRunStartAccepted],
  )

  useEffect(() => {
    transport.setRunStartAcceptedListener(handleRunStartAccepted)
    return () => transport.setRunStartAcceptedListener(undefined)
  }, [handleRunStartAccepted, transport])
  useEffect(() => {
    transport.setStateHydrationListener(handleThreadState)
    return () => transport.setStateHydrationListener(undefined)
  }, [handleThreadState, transport])

  const stream = useStream<MoldyGraphState>({ transport, threadId: conversationId })

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
  }
}
