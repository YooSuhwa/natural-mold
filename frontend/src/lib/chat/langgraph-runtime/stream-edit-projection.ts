import { HumanMessage, type BaseMessage } from '@langchain/core/messages'
import type { AppendMessage } from '@assistant-ui/react'
import type { PendingCheckpointEditSubmit } from './use-checkpoint-fork-handlers'
import {
  branchMetadataFromMessage,
  isHumanMessage,
  messageContentEqualsText,
  textContentFromMessageContent,
} from './stream-message-content'
import {
  messageContentFingerprint,
  messageContentRoleFingerprint,
} from './stream-message-fingerprint'
import { isRecord } from './stream-message-utilities'
import type { ConvertedMessage, VisibleMessageWithId } from './stream-message-types'
import type { PendingEditRenderState } from './stream-edit-reload-types'
import { pendingEditTargetIndex, pendingEditVisibleTarget } from './stream-edit-reload-render'
import { appendMessageText } from './stream-edit-reload-hydration'
import { staleConvertedTailContentFingerprintsForEdit } from './stream-edit-converted'
import {
  clearedBranchPickerCustomMetadata,
  pendingBranchPickerCustomMetadata,
} from './stream-branch-metadata'

export function pendingEditRenderFromAppendMessage(
  conversationId: string,
  message: AppendMessage,
  visibleMessages: readonly VisibleMessageWithId[],
  currentMessages: readonly BaseMessage[],
  convertedMessages: readonly ConvertedMessage[] = [],
): PendingEditRenderState | null {
  const content = appendMessageText(message).trim()
  if (!content) return null
  const target = pendingEditVisibleTarget(message, visibleMessages)
  return pendingEditRenderFromSubmit(
    conversationId,
    {
      content,
      parentId: message.parentId ?? null,
      sourceId: message.sourceId ?? null,
      targetId: target.id,
      targetIndex: target.index,
    },
    currentMessages,
    convertedMessages,
  )
}

export function pendingEditRenderFromSubmit(
  conversationId: string,
  edit: PendingCheckpointEditSubmit,
  currentMessages: readonly BaseMessage[],
  convertedMessages: readonly ConvertedMessage[] = [],
): PendingEditRenderState {
  return {
    conversationId,
    ...edit,
    staleTailFingerprints: staleTailFingerprintsForEdit(currentMessages, edit),
    staleTailContentFingerprints: staleTailContentFingerprintsForEdit(currentMessages, edit),
    staleConvertedTailContentFingerprints: staleConvertedTailContentFingerprintsForEdit(
      convertedMessages,
      edit,
    ),
    requiresLatestBranchMetadata: editRequiresLatestBranchMetadata(currentMessages, edit),
    pendingBranchTotal: pendingEditBranchTotal(currentMessages, edit),
  }
}

export function pendingEditBranchTotal(
  messages: readonly BaseMessage[],
  edit: PendingCheckpointEditSubmit,
): number | null {
  const targetIndex = pendingEditTargetIndex(messages, edit)
  if (targetIndex < 0) return null
  const targetMessage = messages[targetIndex]
  if (!isHumanMessage(targetMessage)) return null
  const metadata = branchMetadataFromMessage(targetMessage)
  const branchTotal = metadata?.branchTotal
  return typeof branchTotal === 'number' && branchTotal >= 2 ? branchTotal + 1 : 2
}

export function editRequiresLatestBranchMetadata(
  messages: readonly BaseMessage[],
  edit: PendingCheckpointEditSubmit,
): boolean {
  const targetIndex = pendingEditTargetIndex(messages, edit)
  const targetMessage = targetIndex >= 0 ? messages[targetIndex] : undefined
  const metadata = branchMetadataFromMessage(targetMessage)
  const branchTotal = metadata?.branchTotal
  return typeof branchTotal === 'number' && branchTotal >= 2
}

export function staleTailFingerprintsForEdit(
  messages: readonly BaseMessage[],
  edit: PendingCheckpointEditSubmit,
): readonly string[] {
  const targetIndex = pendingEditTargetIndex(messages, edit)
  if (targetIndex < 0) return []
  return messages.slice(targetIndex + 1).map(messageContentFingerprint)
}

export function staleTailContentFingerprintsForEdit(
  messages: readonly BaseMessage[],
  edit: PendingCheckpointEditSubmit,
): readonly string[] {
  const targetIndex = pendingEditTargetIndex(messages, edit)
  if (targetIndex < 0) return []
  return messages.slice(targetIndex + 1).map(messageContentRoleFingerprint)
}

export function pendingEditTailAfterStaleMessages(
  messages: readonly BaseMessage[],
  pendingEdit: PendingEditRenderState,
  targetIndex: number,
): readonly BaseMessage[] {
  const tail = messages.slice(targetIndex + 1)
  if (tail.length === 0) return []
  const stalePrefixLength = staleTailPrefixLength(tail, pendingEdit.staleTailFingerprints)
  if (stalePrefixLength > 0) return tail.slice(stalePrefixLength)
  return tail
}

export function staleTailPrefixLength(
  tail: readonly BaseMessage[],
  staleTailFingerprints: readonly string[],
): number {
  const limit = Math.min(tail.length, staleTailFingerprints.length)
  let index = 0
  while (index < limit && messageContentFingerprint(tail[index]) === staleTailFingerprints[index]) {
    index += 1
  }
  return index
}

export function pendingEditOptimisticIndex(
  messages: readonly BaseMessage[],
  pendingEdit: PendingEditRenderState,
  targetIndex: number,
): number {
  for (let index = messages.length - 1; index > targetIndex; index -= 1) {
    const message = messages[index]
    if (
      message &&
      isHumanMessage(message) &&
      (textContentFromMessageContent(message.content) === '' ||
        messageContentEqualsText(message, pendingEdit.content))
    ) {
      return index
    }
  }
  return -1
}

export function editedHumanMessage(
  originalMessage: BaseMessage | undefined,
  pendingEdit: PendingEditRenderState,
): HumanMessage {
  const id = typeof originalMessage?.id === 'string' ? originalMessage.id : pendingEdit.sourceId
  const additionalKwargs = pendingEditAdditionalKwargs(originalMessage, pendingEdit)
  return new HumanMessage({
    content: pendingEdit.content,
    ...(id ? { id } : {}),
    ...(originalMessage?.name ? { name: originalMessage.name } : {}),
    additional_kwargs: additionalKwargs,
    response_metadata: originalMessage?.response_metadata ?? {},
  })
}

export function pendingEditAdditionalKwargs(
  originalMessage: BaseMessage | undefined,
  pendingEdit: PendingEditRenderState,
): Record<string, unknown> {
  const additionalKwargs = isRecord(originalMessage?.additional_kwargs)
    ? originalMessage.additional_kwargs
    : {}
  const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  const nextMetadata =
    pendingEdit.pendingBranchTotal !== null
      ? pendingBranchPickerCustomMetadata(metadata, pendingEdit.pendingBranchTotal, 'pending-edit')
      : clearedBranchPickerCustomMetadata(metadata)
  return {
    ...additionalKwargs,
    metadata: nextMetadata,
  }
}
export function applyPendingEditRenderState(
  messages: readonly BaseMessage[],
  pendingEdit: PendingEditRenderState | null,
): readonly BaseMessage[] {
  if (!pendingEdit) return messages
  const targetIndex = pendingEditTargetIndex(messages, pendingEdit)
  if (targetIndex < 0) return messages

  const optimisticIndex = pendingEditOptimisticIndex(messages, pendingEdit, targetIndex)
  const replacementMessage = editedHumanMessage(messages[targetIndex], pendingEdit)
  if (optimisticIndex > targetIndex) {
    return [
      ...messages.slice(0, targetIndex),
      replacementMessage,
      ...messages.slice(optimisticIndex + 1),
    ]
  }
  return [
    ...messages.slice(0, targetIndex),
    replacementMessage,
    ...pendingEditTailAfterStaleMessages(messages, pendingEdit, targetIndex),
  ]
}
