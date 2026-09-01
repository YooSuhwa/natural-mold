import type { BaseMessage } from '@langchain/core/messages'
import {
  branchMetadataFromMessage,
  isAssistantMessage,
  isHumanMessage,
} from './stream-message-content'
import {
  lastAssistantMessageIndex,
  lastHumanMessageIndex,
  messageRenderKey,
  reloadMessageKey,
  reloadPromptMessageKey,
} from './stream-message-fingerprint'
import type { PendingCheckpointEditSubmit } from './use-checkpoint-fork-handlers'
import { isRecord } from './stream-message-utilities'
import type { VisibleMessageWithId } from './stream-message-types'
import type {
  PendingEditRenderState,
  PendingReloadRenderState,
  PendingReloadRequiredAssistantBranch,
  PendingReloadRequiredUserBranch,
} from './stream-edit-reload-types'

export function activePendingEditRenderState(
  conversationId: string,
  pendingEdit: PendingEditRenderState | null,
): PendingEditRenderState | null {
  return pendingEdit?.conversationId === conversationId ? pendingEdit : null
}

export function activePendingReloadRenderState(
  conversationId: string,
  pendingReload: PendingReloadRenderState | null,
): PendingReloadRenderState | null {
  return pendingReload?.conversationId === conversationId ? pendingReload : null
}

export function applyPendingReloadRenderState(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState | null,
): readonly BaseMessage[] {
  if (!pendingReload) return messages
  const targetIndex = pendingReloadTargetIndex(messages, pendingReload)
  if (targetIndex < 0) return messages
  const targetMessage = messages[targetIndex]
  if (!targetMessage || messageRenderKey(targetMessage) !== pendingReload.staleMessageKey) {
    return messages
  }
  return [...messages.slice(0, targetIndex), ...messages.slice(targetIndex + 1)]
}

export function pendingEditTargetIndex(
  messages: readonly BaseMessage[],
  pendingEdit: Pick<
    PendingCheckpointEditSubmit,
    'parentId' | 'sourceId' | 'targetId' | 'targetIndex'
  >,
): number {
  for (const candidateId of [pendingEdit.sourceId, pendingEdit.parentId, pendingEdit.targetId]) {
    if (!candidateId) continue
    const index = messages.findIndex((message) => message.id === candidateId)
    if (index >= 0) return index
  }
  if (pendingEdit.targetIndex != null) {
    const indexedMessage = messages[pendingEdit.targetIndex]
    if (indexedMessage && isHumanMessage(indexedMessage)) return pendingEdit.targetIndex
  }
  return -1
}

export function pendingEditVisibleTarget(
  detail: Pick<PendingEditRenderState, 'parentId' | 'sourceId'>,
  visibleMessages: readonly VisibleMessageWithId[],
): { readonly id: string | null; readonly index: number | null } {
  for (const candidateId of [detail.sourceId, detail.parentId]) {
    if (!candidateId) continue
    const index = visibleMessages.findIndex((message) => message.id === candidateId)
    if (index >= 0) return { id: candidateId, index }
  }
  return { id: null, index: null }
}

export function pendingReloadRenderFromParentId(
  conversationId: string,
  parentId: string | null,
  visibleMessages: readonly VisibleMessageWithId[],
  currentMessages: readonly BaseMessage[],
): PendingReloadRenderState | null {
  const target = pendingReloadVisibleTarget(parentId, visibleMessages, currentMessages)
  const targetIndex = pendingReloadTargetIndex(currentMessages, target)
  const targetMessage = targetIndex >= 0 ? currentMessages[targetIndex] : undefined
  if (!targetMessage || !isAssistantMessage(targetMessage)) return null
  return {
    conversationId,
    parentId,
    targetId: target.targetId,
    targetIndex,
    promptMessageKey: reloadPromptMessageKey(currentMessages, targetIndex),
    staleMessageKey: messageRenderKey(targetMessage),
    requiredAssistantBranch: requiredAssistantBranchForReload(targetMessage, targetIndex),
    requiredUserBranch: requiredUserBranchForReload(currentMessages, targetIndex),
  }
}

export function requiredAssistantBranchForReload(
  message: BaseMessage,
  targetIndex: number,
): PendingReloadRequiredAssistantBranch {
  const metadata = branchMetadataFromMessage(message)
  const branchTotal = metadata?.branchTotal
  return {
    index: targetIndex,
    branchTotal: typeof branchTotal === 'number' && branchTotal >= 2 ? branchTotal : 1,
  }
}

export function requiredUserBranchForReload(
  messages: readonly BaseMessage[],
  targetIndex: number,
): PendingReloadRequiredUserBranch | null {
  for (let index = targetIndex - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (!isHumanMessage(message)) continue
    const metadata = branchMetadataFromMessage(message)
    const branchTotal = metadata?.branchTotal
    if (typeof branchTotal !== 'number' || branchTotal < 2) return null
    return {
      id: typeof message.id === 'string' && message.id.length > 0 ? message.id : null,
      index,
      branchTotal,
    }
  }
  return null
}

export function pendingReloadVisibleTarget(
  parentId: string | null,
  visibleMessages: readonly VisibleMessageWithId[],
  currentMessages: readonly BaseMessage[],
): Pick<PendingReloadRenderState, 'parentId' | 'targetId' | 'targetIndex'> {
  if (!parentId) {
    const lastAssistantIndex = lastAssistantMessageIndex(currentMessages)
    return {
      parentId,
      targetId: currentMessages[lastAssistantIndex]?.id ?? null,
      targetIndex: lastAssistantIndex >= 0 ? lastAssistantIndex : null,
    }
  }

  const directMessageIndex = currentMessages.findIndex((message) => message.id === parentId)
  if (directMessageIndex >= 0 && isAssistantMessage(currentMessages[directMessageIndex])) {
    return { parentId, targetId: parentId, targetIndex: directMessageIndex }
  }
  const nextMessage = currentMessages[directMessageIndex + 1]
  if (directMessageIndex >= 0 && nextMessage && isAssistantMessage(nextMessage)) {
    return { parentId, targetId: nextMessage.id ?? null, targetIndex: directMessageIndex + 1 }
  }

  const visibleIndex = visibleMessages.findIndex((message) => message.id === parentId)
  const visibleMessage = visibleIndex >= 0 ? visibleMessages[visibleIndex] : undefined
  if (isVisibleAssistantMessage(visibleMessage)) {
    return { parentId, targetId: parentId, targetIndex: visibleIndex }
  }
  const nextVisibleMessage = visibleMessages[visibleIndex + 1]
  if (visibleIndex >= 0 && isVisibleAssistantMessage(nextVisibleMessage)) {
    return { parentId, targetId: nextVisibleMessage.id, targetIndex: visibleIndex + 1 }
  }

  return { parentId, targetId: null, targetIndex: null }
}

export function pendingReloadTargetIndex(
  messages: readonly BaseMessage[],
  pendingReload: Pick<PendingReloadRenderState, 'targetId' | 'targetIndex'>,
): number {
  if (pendingReload.targetId) {
    const index = messages.findIndex((message) => message.id === pendingReload.targetId)
    if (index >= 0) return index
  }
  if (pendingReload.targetIndex != null) {
    const indexedMessage = messages[pendingReload.targetIndex]
    if (indexedMessage && isAssistantMessage(indexedMessage)) return pendingReload.targetIndex
  }
  return -1
}

export function pendingReloadBranchMetadataTargetIndex(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState,
): number {
  const targetIndex = pendingReloadTargetIndex(messages, pendingReload)
  if (targetIndex >= 0) return targetIndex
  return pendingReloadReplacementAssistantIndex(messages, pendingReload)
}

export function pendingReloadReplacementAssistantIndex(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState,
): number {
  const assistantIndex = lastAssistantMessageIndex(messages)
  if (assistantIndex < 0) return -1
  const lastHumanIndex = lastHumanMessageIndex(messages)
  if (lastHumanIndex > assistantIndex) return -1
  if (messages.length === 1) return assistantIndex
  if (!pendingReload.promptMessageKey) return -1
  const promptIndex = messages.findIndex(
    (message, index) =>
      index < assistantIndex &&
      isHumanMessage(message) &&
      reloadMessageKey(message) === pendingReload.promptMessageKey,
  )
  return promptIndex >= 0 ? assistantIndex : -1
}
export function isVisibleAssistantMessage(
  message: (VisibleMessageWithId & { readonly role?: unknown }) | undefined,
): boolean {
  return isRecord(message) && message.role === 'assistant'
}
