'use client'

import { useEffect, useMemo } from 'react'
import type { BaseMessage } from '@langchain/core/messages'
import type { PendingNewSubmitState } from './use-submit-checkpoint-controller'
import type { PendingEditRenderState, PendingReloadRenderState } from './stream-edit-reload-types'
import type { ServerMessageMetadataSnapshot } from './stream-thread-state-projection'
import {
  appendPendingNewSubmitMessage,
  isHumanMessage,
  messageContentEqualsText,
} from './stream-message-projection'
import {
  messageListIsDegraded,
  suppressRunningEmptyAssistantPlaceholder,
} from './stream-message-comparison'
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
  const acknowledged =
    pendingSubmit !== null &&
    streamMessages.some(
      (message) =>
        isHumanMessage(message) && messageContentEqualsText(message, pendingSubmit.content),
    )
  useEffect(() => {
    if (!pendingSubmit || !acknowledged || pendingSubmit.attemptId === undefined) return
    clearPendingSubmit(pendingSubmit.content, pendingSubmit.attemptId)
  }, [acknowledged, clearPendingSubmit, pendingSubmit])

  const visibleWithPending = useMemo(
    () => appendPendingNewSubmitMessage(streamMessages, acknowledged ? null : pendingSubmit),
    [acknowledged, pendingSubmit, streamMessages],
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
    if (settling || fallback.length === 0) return visible
    return messageListIsDegraded(visible, fallback) ? fallback : visible
  }, [fallback, settling, visible])
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
