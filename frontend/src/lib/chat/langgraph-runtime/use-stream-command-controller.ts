'use client'

import { useCallback, useRef } from 'react'
import { flushSync } from 'react-dom'
import type { AppendMessage } from '@assistant-ui/react'
import {
  appendMessageHasAttachments,
  appendMessageText,
  pendingEditRenderFromAppendMessage,
  pendingEditRenderFromSubmit,
  pendingReloadRenderFromParentId,
} from './stream-edit-reload-projection'
import { clearStickyConversationMessages } from './stream-message-projection'
import {
  useCheckpointForkHandlers,
  type PendingCheckpointEditSubmit,
} from './use-checkpoint-fork-handlers'
import { useStreamCancelController } from './use-stream-cancel-controller'
import type { UseStreamCommandControllerOptions } from './stream-command-types'

export function useStreamCommandController({
  conversationId,
  stream,
  visibleMessages,
  langChainMessages,
  convertedMessages,
  onBeforeSubmit,
  setChatCancelInFlight,
  clearBranchPickerSuppression,
  submitCheckpoint,
  reconciliation,
}: UseStreamCommandControllerOptions) {
  const activeEditAttemptRef = useRef<number | null>(null)
  const refinePendingEdit = useCallback(
    (edit: PendingCheckpointEditSubmit): void => {
      const attemptId = activeEditAttemptRef.current
      if (attemptId === null) return
      const base = reconciliation.getPendingEditBase()
      const baseMessages =
        base.messages ??
        (reconciliation.getLatestVisibleMessages().length > 0
          ? reconciliation.getLatestVisibleMessages()
          : langChainMessages)
      reconciliation.updatePendingEdit(
        conversationId,
        attemptId,
        pendingEditRenderFromSubmit(
          conversationId,
          edit,
          baseMessages,
          base.convertedMessages ?? convertedMessages,
        ),
      )
    },
    [conversationId, convertedMessages, langChainMessages, reconciliation],
  )

  const {
    onNew: submitNew,
    onEdit: submitEdit,
    onReload: submitReload,
  } = useCheckpointForkHandlers({
    conversationId,
    stream,
    visibleMessages,
    langChainMessages,
    onBeforeEditSubmit: refinePendingEdit,
  })

  const onNew = useCallback(
    async (...args: Parameters<typeof submitNew>): Promise<void> => {
      const content = appendMessageText(args[0]).trim()
      const hasAttachments = appendMessageHasAttachments(args[0])
      if (content.length === 0 && !hasAttachments) {
        await submitNew(...args)
        return
      }
      onBeforeSubmit?.()
      reconciliation.cancelPostRunHydration()
      reconciliation.setThreadRunNotice(null)
      reconciliation.clearServerHydrationState()
      clearBranchPickerSuppression()
      reconciliation.clearPendingEdit(conversationId)
      reconciliation.clearPendingReload(conversationId)

      let pendingAttemptId: number | null = null
      if (content.length > 0) {
        flushSync(() => {
          pendingAttemptId =
            submitCheckpoint.beginPendingSubmit(
              content,
              reconciliation.getLatestVisibleMessages().length,
            ).attemptId ?? null
        })
      }
      try {
        await submitNew(...args)
      } catch (caught) {
        if (pendingAttemptId !== null) {
          if (submitCheckpoint.clearPendingSubmit(content, pendingAttemptId)) {
            clearStickyConversationMessages(conversationId)
          }
        }
        throw caught
      }
    },
    [
      clearBranchPickerSuppression,
      conversationId,
      onBeforeSubmit,
      reconciliation,
      submitCheckpoint,
      submitNew,
    ],
  )

  const onEdit = useCallback(
    async (message: AppendMessage): Promise<void> => {
      reconciliation.setThreadRunNotice(null)
      reconciliation.clearServerHydrationState()
      reconciliation.clearPendingReload(conversationId)
      submitCheckpoint.clearConversationPendingSubmit()
      const latest = reconciliation.getLatestVisibleMessages()
      const baseMessages = latest.length > 0 ? latest : langChainMessages
      reconciliation.stagePendingEditBase(baseMessages, convertedMessages)
      let attemptId: number | null = null
      flushSync(() => {
        const pending = pendingEditRenderFromAppendMessage(
          conversationId,
          message,
          visibleMessages,
          baseMessages,
          convertedMessages,
        )
        if (pending) attemptId = reconciliation.beginPendingEdit(pending)
        activeEditAttemptRef.current = attemptId
      })
      clearStickyConversationMessages(conversationId)
      try {
        const submitted = await submitEdit(message)
        if (!submitted && attemptId !== null) {
          reconciliation.clearPendingEdit(conversationId, attemptId)
        }
      } catch (caught) {
        if (attemptId !== null) reconciliation.clearPendingEdit(conversationId, attemptId)
        throw caught
      } finally {
        if (activeEditAttemptRef.current === attemptId) activeEditAttemptRef.current = null
      }
    },
    [
      conversationId,
      convertedMessages,
      langChainMessages,
      reconciliation,
      submitCheckpoint,
      submitEdit,
      visibleMessages,
    ],
  )

  const onReload = useCallback(
    async (parentId: string | null): Promise<void> => {
      reconciliation.setThreadRunNotice(null)
      reconciliation.clearServerHydrationState()
      clearBranchPickerSuppression()
      reconciliation.clearPendingEdit(conversationId)
      submitCheckpoint.clearConversationPendingSubmit()
      let attemptId: number | null = null
      flushSync(() => {
        const pending = pendingReloadRenderFromParentId(
          conversationId,
          parentId,
          visibleMessages,
          langChainMessages,
        )
        if (pending) attemptId = reconciliation.beginPendingReload(pending)
      })
      clearStickyConversationMessages(conversationId)
      try {
        const submitted = await submitReload(parentId)
        if (!submitted && attemptId !== null) {
          reconciliation.clearPendingReload(conversationId, attemptId)
        }
      } catch (caught) {
        if (attemptId !== null) reconciliation.clearPendingReload(conversationId, attemptId)
        throw caught
      }
    },
    [
      clearBranchPickerSuppression,
      conversationId,
      langChainMessages,
      reconciliation,
      submitCheckpoint,
      submitReload,
      visibleMessages,
    ],
  )

  const onCancel = useStreamCancelController({
    conversationId,
    stream,
    reconciliation,
    acceptPendingSubmit: submitCheckpoint.acceptPendingSubmit,
    setChatCancelInFlight,
  })
  return { onNew, onEdit, onReload, onCancel }
}
