'use client'

import { HumanMessage } from '@langchain/core/messages'
import { useCallback, useSyncExternalStore } from 'react'

export interface PendingNewSubmitState {
  readonly conversationId: string
  readonly attemptId?: number
  readonly content: string
  readonly baseMessageCount: number
  readonly message: HumanMessage
}

const PENDING_SUBMIT_LIMIT = 50
const pendingSubmitsByConversation = new Map<string, PendingNewSubmitState>()
const listeners = new Set<() => void>()
let nextAttemptId = 1

export function useSubmitCheckpointController(conversationId: string) {
  const getSnapshot = useCallback(
    () => pendingSubmitsByConversation.get(conversationId) ?? null,
    [conversationId],
  )
  const pendingSubmit = useSyncExternalStore(subscribe, getSnapshot, emptySnapshot)

  const beginPendingSubmit = useCallback(
    (content: string, baseMessageCount: number): PendingNewSubmitState => {
      const attemptId = nextAttemptId++
      const pending: PendingNewSubmitState = {
        conversationId,
        attemptId,
        content,
        baseMessageCount,
        message: new HumanMessage({
          id: `moldy-pending-user:${conversationId}:${Date.now()}`,
          content,
        }),
      }
      pendingSubmitsByConversation.set(conversationId, pending)
      if (pendingSubmitsByConversation.size > PENDING_SUBMIT_LIMIT) {
        const oldestKey = pendingSubmitsByConversation.keys().next().value
        if (typeof oldestKey === 'string') pendingSubmitsByConversation.delete(oldestKey)
      }
      emitChange()
      return pending
    },
    [conversationId],
  )

  const clearPendingSubmit = useCallback(
    (content: string, attemptId: number): boolean => {
      const current = pendingSubmitsByConversation.get(conversationId)
      if (current?.content !== content || current.attemptId !== attemptId) return false
      pendingSubmitsByConversation.delete(conversationId)
      emitChange()
      return true
    },
    [conversationId],
  )

  const clearConversationPendingSubmit = useCallback((): void => {
    if (!pendingSubmitsByConversation.delete(conversationId)) return
    emitChange()
  }, [conversationId])

  return {
    pendingSubmit,
    beginPendingSubmit,
    clearPendingSubmit,
    clearConversationPendingSubmit,
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function emptySnapshot(): PendingNewSubmitState | null {
  return null
}

function emitChange(): void {
  for (const listener of listeners) listener()
}
