'use client'

import { useEffect, useMemo } from 'react'
import { HumanMessage, type BaseMessage } from '@langchain/core/messages'
import type { PendingNewSubmitState } from './use-submit-checkpoint-controller'
import type { PendingEditRenderState, PendingReloadRenderState } from './stream-edit-reload-types'
import type { ServerMessageMetadataSnapshot } from './stream-thread-state-projection'
import {
  appendPendingNewSubmitMessage,
  isHumanMessage,
  messageContentEqualsText,
} from './stream-message-projection'
import {
  isEmptyMessageContent,
  messageListIsDegraded,
  suppressRunningEmptyAssistantPlaceholder,
} from './stream-message-comparison'
import { branchMetadataFromMessage } from './stream-message-content'
import { applyPendingReloadRenderState } from './stream-edit-reload-render'
import { applyPendingEditRenderState } from './stream-edit-projection'
import {
  applyPendingEditBranchMetadata,
  applyPendingReloadBranchMetadata,
  mergeServerMessageMetadata,
} from './stream-branch-metadata'
import { messagesFromServerMessages } from './stream-thread-state-projection'
import type { Message as MoldyMessage } from '@/lib/types'

interface UseStreamVisibleMessagesOptions {
  readonly streamMessages: readonly BaseMessage[]
  readonly isLoading: boolean
  readonly postRunHydrationPending: boolean
  readonly serverMessages?: readonly MoldyMessage[]
  readonly serverMessageMetadata: ServerMessageMetadataSnapshot
  readonly pendingSubmit: PendingNewSubmitState | null
  readonly clearPendingSubmit: (content: string, attemptId: number) => boolean
  readonly pendingEdit: PendingEditRenderState | null
  readonly pendingReload: PendingReloadRenderState | null
}

export function useStreamVisibleMessages({
  streamMessages,
  isLoading,
  postRunHydrationPending,
  serverMessages,
  serverMessageMetadata,
  pendingSubmit,
  clearPendingSubmit,
  pendingEdit,
  pendingReload,
}: UseStreamVisibleMessagesOptions) {
  const streamAcknowledged =
    pendingSubmit !== null &&
    streamMessages.some(
      (message) =>
        isHumanMessage(message) && messageContentEqualsText(message, pendingSubmit.content),
    )
  const serverAcknowledged =
    pendingSubmit !== null &&
    (serverMessages ?? []).some(
      (message) => message.role === 'user' && message.content === pendingSubmit.content,
    )
  useEffect(() => {
    // A values snapshot can briefly include the user message and then regress
    // during run promotion or interruption. Keep the optimistic checkpoint
    // until the independently fetched message envelope confirms persistence.
    if (!pendingSubmit || !serverAcknowledged || pendingSubmit.attemptId === undefined) return
    clearPendingSubmit(pendingSubmit.content, pendingSubmit.attemptId)
  }, [clearPendingSubmit, pendingSubmit, serverAcknowledged])

  const visibleWithPending = useMemo(
    () => appendPendingNewSubmitMessage(streamMessages, streamAcknowledged ? null : pendingSubmit),
    [pendingSubmit, streamAcknowledged, streamMessages],
  )
  const visible = useMemo(
    () =>
      applyPendingReloadRenderState(
        applyPendingEditRenderState(visibleWithPending, pendingEdit),
        pendingReload,
      ),
    [pendingEdit, pendingReload, visibleWithPending],
  )
  const fallback = useMemo(() => messagesFromServerMessages(serverMessages), [serverMessages])
  const settling = isLoading || postRunHydrationPending
  const reconciled = useMemo(() => {
    if (fallback.length === 0) return visible
    // A live interrupt can publish a thread-state snapshot before the durable
    // message envelope catches up. Preserve any user message already confirmed
    // by that envelope, while edit/reload projections continue to own their
    // intentionally shorter branch during settlement.
    if (settling && (pendingEdit !== null || pendingReload !== null)) return visible
    if (messageListIsDegraded(visible, fallback)) return fallback
    // Durable replay may contain only human id/type references. Restore their
    // content from the message envelope without replacing newer stream output.
    const persisted = new Map<string, BaseMessage>()
    const ambiguous = new Set<string>()
    for (const message of fallback) {
      if (!message.id || !isHumanMessage(message)) continue
      if (persisted.has(message.id)) ambiguous.add(message.id)
      persisted.set(message.id, message)
    }
    return visible.map((message) => {
      if (
        !message.id ||
        ambiguous.has(message.id) ||
        !isHumanMessage(message) ||
        !isEmptyMessageContent(message.content)
      )
        return message
      const source = persisted.get(message.id)
      return source && !isEmptyMessageContent(source.content)
        ? new HumanMessage({
            ...message,
            content: source.content,
            additional_kwargs: {
              ...message.additional_kwargs,
              metadata: {
                ...branchMetadataFromMessage(message),
                publicMessageId: branchMetadataFromMessage(source).publicMessageId,
              },
            },
          })
        : message
    })
  }, [fallback, pendingEdit, pendingReload, settling, visible])
  const renderable = useMemo(
    () => suppressRunningEmptyAssistantPlaceholder(reconciled, isLoading),
    [isLoading, reconciled],
  )
  const messages = useMemo(
    () =>
      applyPendingReloadBranchMetadata(
        applyPendingEditBranchMetadata(
          mergeServerMessageMetadata(renderable, serverMessageMetadata),
          pendingEdit,
        ),
        pendingReload,
      ),
    [pendingEdit, pendingReload, renderable, serverMessageMetadata],
  )
  return { messages, settling }
}
