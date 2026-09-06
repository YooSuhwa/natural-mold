'use client'

import { useCallback, useRef, useState } from 'react'
import type { BaseMessage } from '@langchain/core/messages'
import {
  activePendingEditRenderState,
  activePendingReloadRenderState,
} from './stream-edit-reload-render'
import type { PendingEditRenderState, PendingReloadRenderState } from './stream-edit-reload-types'
import { snapshotBaseMessages, type ConvertedMessage } from './stream-message-projection'
import type { ReloadRunCorrelation } from './stream-thread-state-projection'

interface PendingOperation<T> {
  readonly conversationId: string
  readonly attemptId: number
  readonly value: T
}

export function useReconciliationOperationState(
  conversationId: string,
  clearBranchPickerSuppression: () => void,
) {
  const [pendingEditState, setPendingEditState] =
    useState<PendingOperation<PendingEditRenderState> | null>(null)
  const [pendingReloadState, setPendingReloadState] =
    useState<PendingOperation<PendingReloadRenderState> | null>(null)
  const [reloadRunCorrelation, setReloadRunCorrelation] = useState<ReloadRunCorrelation>({
    conversationId,
    pendingReload: false,
    acceptedRunId: null,
  })
  const nextAttemptIdRef = useRef(1)
  const pendingEditRef = useRef<PendingOperation<PendingEditRenderState> | null>(null)
  const pendingReloadRef = useRef<PendingOperation<PendingReloadRenderState> | null>(null)
  const reloadCorrelationAttemptRef = useRef<number | null>(null)
  const reloadRunCorrelationRef = useRef(reloadRunCorrelation)
  const latestVisibleMessagesRef = useRef<readonly BaseMessage[]>([])
  const pendingEditBaseMessagesRef = useRef<readonly BaseMessage[] | null>(null)
  const pendingEditBaseConvertedMessagesRef = useRef<readonly ConvertedMessage[] | null>(null)

  const beginPendingEdit = useCallback((value: PendingEditRenderState): number => {
    const attemptId = nextAttemptIdRef.current++
    const operation = { conversationId: value.conversationId, attemptId, value }
    pendingEditRef.current = operation
    setPendingEditState(operation)
    return attemptId
  }, [])
  const updatePendingEdit = useCallback(
    (targetConversationId: string, attemptId: number, value: PendingEditRenderState): void => {
      const current = pendingEditRef.current
      if (current?.conversationId !== targetConversationId || current.attemptId !== attemptId)
        return
      const operation = { conversationId: targetConversationId, attemptId, value }
      pendingEditRef.current = operation
      setPendingEditState(operation)
    },
    [],
  )
  const clearPendingEdit = useCallback(
    (targetConversationId: string, attemptId?: number): void => {
      const operation = pendingEditRef.current
      if (operation?.conversationId !== targetConversationId) return
      if (attemptId !== undefined && operation.attemptId !== attemptId) return
      pendingEditRef.current = null
      setPendingEditState((current) =>
        current?.conversationId === targetConversationId &&
        (attemptId === undefined || current.attemptId === attemptId)
          ? null
          : current,
      )
      pendingEditBaseMessagesRef.current = null
      pendingEditBaseConvertedMessagesRef.current = null
      if (targetConversationId === conversationId) clearBranchPickerSuppression()
    },
    [clearBranchPickerSuppression, conversationId],
  )
  const beginPendingReload = useCallback((value: PendingReloadRenderState): number => {
    const attemptId = nextAttemptIdRef.current++
    const correlation = {
      conversationId: value.conversationId,
      pendingReload: true,
      acceptedRunId: null,
    }
    reloadRunCorrelationRef.current = correlation
    reloadCorrelationAttemptRef.current = attemptId
    setReloadRunCorrelation(correlation)
    const operation = { conversationId: value.conversationId, attemptId, value }
    pendingReloadRef.current = operation
    setPendingReloadState(operation)
    return attemptId
  }, [])
  const clearPendingReload = useCallback(
    (targetConversationId: string, attemptId?: number): void => {
      const operation = pendingReloadRef.current
      if (operation?.conversationId !== targetConversationId) return
      if (attemptId !== undefined && operation.attemptId !== attemptId) return
      pendingReloadRef.current = null
      setPendingReloadState((current) =>
        current?.conversationId === targetConversationId &&
        (attemptId === undefined || current.attemptId === attemptId)
          ? null
          : current,
      )
      const current = reloadRunCorrelationRef.current
      if (
        current.conversationId === targetConversationId &&
        current.pendingReload &&
        (attemptId === undefined || reloadCorrelationAttemptRef.current === attemptId)
      ) {
        const next = { ...current, pendingReload: false }
        reloadRunCorrelationRef.current = next
        reloadCorrelationAttemptRef.current = null
        setReloadRunCorrelation(next)
      }
    },
    [],
  )
  const acceptReloadRun = useCallback(
    (runId: string): void => {
      const current = reloadRunCorrelationRef.current
      const next = {
        conversationId,
        pendingReload: current.conversationId === conversationId && current.pendingReload,
        acceptedRunId: runId,
      }
      reloadRunCorrelationRef.current = next
      setReloadRunCorrelation(next)
    },
    [conversationId],
  )
  const recordLatestVisibleMessages = useCallback((messages: readonly BaseMessage[]) => {
    latestVisibleMessagesRef.current = snapshotBaseMessages(messages)
  }, [])
  const getLatestVisibleMessages = useCallback(() => latestVisibleMessagesRef.current, [])
  const stagePendingEditBase = useCallback(
    (messages: readonly BaseMessage[], convertedMessages: readonly ConvertedMessage[]) => {
      pendingEditBaseMessagesRef.current = messages
      pendingEditBaseConvertedMessagesRef.current = convertedMessages
    },
    [],
  )
  const getPendingEditBase = useCallback(
    () => ({
      messages: pendingEditBaseMessagesRef.current,
      convertedMessages: pendingEditBaseConvertedMessagesRef.current,
    }),
    [],
  )
  return {
    pendingEditState,
    pendingReloadState,
    pendingEditRender: activePendingEditRenderState(
      conversationId,
      pendingEditState?.value ?? null,
    ),
    pendingReloadRender: activePendingReloadRenderState(
      conversationId,
      pendingReloadState?.value ?? null,
    ),
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
    getPendingEditAttemptId: () =>
      pendingEditRef.current?.conversationId === conversationId
        ? pendingEditRef.current.attemptId
        : null,
    getPendingReloadAttemptId: () =>
      pendingReloadRef.current?.conversationId === conversationId
        ? pendingReloadRef.current.attemptId
        : null,
  }
}
