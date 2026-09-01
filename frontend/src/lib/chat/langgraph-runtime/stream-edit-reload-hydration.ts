import type { BaseMessage } from '@langchain/core/messages'
import {
  branchMetadataFromMessage,
  isAssistantMessage,
  isHumanMessage,
  messageContentEqualsText,
} from './stream-message-content'
import { isEmptyAssistantMessage } from './stream-message-comparison'
import { messageRenderKey } from './stream-message-fingerprint'
import { isRecord } from './stream-message-utilities'
import {
  messagesFromThreadState,
  terminalRunNoticeFromThreadState,
  type ReloadRunCorrelation,
} from './stream-thread-state-projection'
import type { PendingEditRenderState, PendingReloadRenderState } from './stream-edit-reload-types'
import { pendingEditTargetIndex, pendingReloadTargetIndex } from './stream-edit-reload-render'

export function pendingEditHydratedMessage(
  messages: readonly BaseMessage[],
  pendingEdit: PendingEditRenderState,
): BaseMessage | undefined {
  const targetIndex = pendingEditTargetIndex(messages, pendingEdit)
  const targetMessage = targetIndex >= 0 ? messages[targetIndex] : undefined
  if (messageContentEqualsText(targetMessage, pendingEdit.content)) return targetMessage
  return messages.find(
    (message) => isHumanMessage(message) && messageContentEqualsText(message, pendingEdit.content),
  )
}

export function pendingEditHydrationIsReady(
  state: unknown,
  pendingEdit: PendingEditRenderState,
): boolean {
  const messages = messagesFromThreadState(state)
  if (!messages) return false
  const targetMessage = pendingEditHydratedMessage(messages, pendingEdit)
  if (!targetMessage) return false
  const targetIndex = messages.findIndex((message) => message === targetMessage)
  if (targetIndex < 0 || targetIndex >= messages.length - 1) return false
  const metadata = branchMetadataFromMessage(targetMessage)
  const branchIndex = metadata?.branchIndex
  const branchTotal = metadata?.branchTotal
  if (pendingEdit.requiresLatestBranchMetadata) {
    return (
      typeof branchTotal === 'number' &&
      branchTotal >= 2 &&
      typeof branchIndex === 'number' &&
      branchIndex === branchTotal - 1
    )
  }
  if (typeof branchTotal === 'number' && branchTotal >= 2) {
    return typeof branchIndex === 'number' && branchIndex === branchTotal - 1
  }
  return true
}

export function pendingReloadHydrationIsReady(
  state: unknown,
  pendingReload: PendingReloadRenderState,
  correlation: ReloadRunCorrelation,
): boolean {
  const messages = messagesFromThreadState(state)
  if (!messages) return false
  // A failed reload has no replacement assistant turn to satisfy the normal
  // branch-metadata predicate. Its terminal run state is the completed result
  // that must replace the optimistic reload render so the error notice can
  // remain visible and retryable.
  const terminalRunNotice = terminalRunNoticeFromThreadState(state)
  if (terminalRunNotice?.status === 'failed') {
    return (
      correlation.conversationId === pendingReload.conversationId &&
      correlation.acceptedRunId === terminalRunNotice.id
    )
  }
  const targetIndex = pendingReloadTargetIndex(messages, pendingReload)
  if (targetIndex < 0) return false
  const targetMessage = messages[targetIndex]
  return (
    !!targetMessage &&
    isAssistantMessage(targetMessage) &&
    messageRenderKey(targetMessage) !== pendingReload.staleMessageKey &&
    !isEmptyAssistantMessage(targetMessage) &&
    pendingReloadAssistantBranchHydrationIsReady(messages, pendingReload) &&
    pendingReloadUserBranchHydrationIsReady(messages, pendingReload)
  )
}

export function pendingReloadAssistantBranchHydrationIsReady(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState,
): boolean {
  const required = pendingReload.requiredAssistantBranch
  const message = messages[required.index]
  if (!isAssistantMessage(message)) return false
  const metadata = branchMetadataFromMessage(message)
  const branchIndex = metadata?.branchIndex
  const branchTotal = metadata?.branchTotal
  return (
    typeof branchTotal === 'number' &&
    branchTotal >= required.branchTotal + 1 &&
    typeof branchIndex === 'number' &&
    branchIndex === branchTotal - 1
  )
}

export function pendingReloadUserBranchHydrationIsReady(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState,
): boolean {
  const required = pendingReload.requiredUserBranch
  if (!required) return true
  const message =
    (required.id ? messages.find((candidate) => candidate.id === required.id) : undefined) ??
    messages[required.index]
  if (!isHumanMessage(message)) return false
  const metadata = branchMetadataFromMessage(message)
  const branchIndex = metadata?.branchIndex
  const branchTotal = metadata?.branchTotal
  return (
    typeof branchTotal === 'number' &&
    branchTotal >= required.branchTotal &&
    typeof branchIndex === 'number' &&
    branchIndex === branchTotal - 1
  )
}

export function appendMessageText(message: {
  content: readonly unknown[]
  attachments?: readonly { content?: readonly unknown[] }[]
}): string {
  const content = [
    ...message.content,
    ...(message.attachments?.flatMap((attachment) => attachment.content) ?? []),
  ]
  return content
    .map((part) => {
      if (typeof part === 'string') return part
      if (!isRecord(part)) return ''
      return typeof part.text === 'string' ? part.text : ''
    })
    .join('')
}

export function appendMessageHasAttachments(message: {
  attachments?: readonly unknown[]
}): boolean {
  return Array.isArray(message.attachments) && message.attachments.length > 0
}
