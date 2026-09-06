import {
  convertedMessageContentRoleFingerprint,
  convertedMessageId,
  convertedMessageText,
  isConvertedUserMessage,
} from './stream-message-fingerprint'
import { isRecord } from './stream-message-utilities'
import { stableString } from './message-list'
import type { ConvertedMessage } from './stream-message-types'
import type { PendingEditRenderState } from './stream-edit-reload-types'
import type { PendingCheckpointEditSubmit } from './use-checkpoint-fork-handlers'
import {
  clearedBranchPickerCustomMetadata,
  pendingBranchPickerCustomMetadata,
} from './stream-branch-metadata'

export function suppressPendingEditConvertedDuplicate(
  messages: readonly ConvertedMessage[],
  pendingEdit: PendingEditRenderState | null,
): readonly ConvertedMessage[] {
  if (!pendingEdit) return messages
  const targetIndex = pendingEditConvertedTargetIndex(messages, pendingEdit)
  if (targetIndex < 0) return messages
  const duplicateIndex = pendingEditConvertedDuplicateIndex(messages, pendingEdit, targetIndex)
  if (duplicateIndex <= targetIndex) return messages
  return [...messages.slice(0, duplicateIndex), ...messages.slice(duplicateIndex + 1)]
}

export function suppressPendingEditConvertedStaleTail(
  messages: readonly ConvertedMessage[],
  pendingEdit: PendingEditRenderState | null,
): readonly ConvertedMessage[] {
  if (!pendingEdit) return messages
  const staleTailContentFingerprints =
    pendingEdit.staleConvertedTailContentFingerprints.length > 0
      ? pendingEdit.staleConvertedTailContentFingerprints
      : pendingEdit.staleTailContentFingerprints
  if (staleTailContentFingerprints.length === 0) return messages
  const targetIndex = pendingEditConvertedTargetIndex(messages, pendingEdit)
  if (targetIndex < 0) return messages
  const tail = messages.slice(targetIndex + 1)
  const stalePrefixLength = convertedStaleTailPrefixLength(tail, staleTailContentFingerprints)
  if (stalePrefixLength <= 0) return messages
  return [
    ...messages.slice(0, targetIndex + 1),
    ...tail.slice(stalePrefixLength),
  ] as readonly ConvertedMessage[]
}

export function convertedStaleTailPrefixLength(
  tail: readonly ConvertedMessage[],
  staleTailContentFingerprints: readonly string[],
): number {
  const limit = Math.min(tail.length, staleTailContentFingerprints.length)
  let index = 0
  while (
    index < limit &&
    convertedMessageContentRoleFingerprint(tail[index]) === staleTailContentFingerprints[index]
  ) {
    index += 1
  }
  return index
}

export function applyPendingEditConvertedBranchMetadata(
  messages: readonly ConvertedMessage[],
  pendingEdit: PendingEditRenderState | null,
): readonly ConvertedMessage[] {
  if (!pendingEdit) return messages
  const targetIndex = pendingEditConvertedTargetIndex(messages, pendingEdit)
  if (targetIndex < 0) return messages
  const targetMessage = messages[targetIndex]
  if (!targetMessage) return messages
  if (convertedMessageText(targetMessage) !== pendingEdit.content) return messages
  const replacement = cloneConvertedMessageWithPendingEditBranchMetadata(targetMessage, pendingEdit)
  if (!replacement || replacement === targetMessage) return messages
  return [...messages.slice(0, targetIndex), replacement, ...messages.slice(targetIndex + 1)]
}

export function cloneConvertedMessageWithPendingEditBranchMetadata(
  message: ConvertedMessage | undefined,
  pendingEdit: PendingEditRenderState,
): ConvertedMessage | undefined {
  if (!isRecord(message)) return message
  const metadata: Record<string, unknown> = isRecord(message.metadata) ? message.metadata : {}
  const customValue = metadata.custom
  const custom = isRecord(customValue) ? customValue : {}
  const nextCustom =
    pendingEdit.pendingBranchTotal !== null
      ? pendingBranchPickerCustomMetadata(custom, pendingEdit.pendingBranchTotal, 'pending-edit')
      : clearedBranchPickerCustomMetadata(custom)
  const nextMetadata = {
    ...metadata,
    custom: nextCustom,
  }
  if (stableString(nextMetadata) === stableString(message.metadata)) return message
  return {
    ...message,
    metadata: nextMetadata,
  } as ConvertedMessage
}

export function pendingEditConvertedTargetIndex(
  messages: readonly ConvertedMessage[],
  pendingEdit: Pick<
    PendingCheckpointEditSubmit,
    'parentId' | 'sourceId' | 'targetId' | 'targetIndex'
  >,
): number {
  for (const candidateId of [pendingEdit.sourceId, pendingEdit.parentId, pendingEdit.targetId]) {
    if (!candidateId) continue
    const index = messages.findIndex((message) => convertedMessageId(message) === candidateId)
    if (index >= 0) return index
  }
  if (
    pendingEdit.targetIndex != null &&
    isConvertedUserMessage(messages[pendingEdit.targetIndex])
  ) {
    return pendingEdit.targetIndex
  }
  return -1
}

export function pendingEditConvertedDuplicateIndex(
  messages: readonly ConvertedMessage[],
  pendingEdit: PendingEditRenderState,
  targetIndex: number,
): number {
  for (let index = messages.length - 1; index > targetIndex; index -= 1) {
    const message = messages[index]
    if (!isConvertedUserMessage(message)) continue
    const text = convertedMessageText(message)
    if (text === '' || text === pendingEdit.content) return index
  }
  return -1
}
export function staleConvertedTailContentFingerprintsForEdit(
  messages: readonly ConvertedMessage[],
  edit: PendingCheckpointEditSubmit,
): readonly string[] {
  const targetIndex = pendingEditConvertedTargetIndex(messages, edit)
  if (targetIndex < 0) return []
  return messages.slice(targetIndex + 1).map(convertedMessageContentRoleFingerprint)
}
