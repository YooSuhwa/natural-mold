import { AIMessage, type BaseMessage } from '@langchain/core/messages'
import { isAssistantMessage, messageContentEqualsText } from './stream-message-content'
import { isRecord } from './stream-message-utilities'
import { stableString } from './message-list'
import {
  TERMINAL_NOTICE_BOUNDARY_METADATA_KEY,
  TERMINAL_NOTICE_METADATA_KEY,
} from './terminal-notice'
import type {
  ServerMessageMetadataSnapshot,
  ThreadRunNotice,
} from './stream-thread-state-projection'
import type { PendingEditRenderState, PendingReloadRenderState } from './stream-edit-reload-types'
import {
  pendingEditTargetIndex,
  pendingReloadBranchMetadataTargetIndex,
} from './stream-edit-reload-render'

export const BRANCH_PICKER_METADATA_KEYS = [
  'branches',
  'siblingCheckpointIds',
  'activeBranchId',
  'branchCheckpointId',
  'branchIndex',
  'branchTotal',
  'checkpoint_id',
  'moldyBranchPickerDisplayOnly',
] as const
export const CLEARED_BRANCH_PICKER_METADATA = {
  branches: [],
  siblingCheckpointIds: [],
  activeBranchId: null,
  branchCheckpointId: null,
  branchIndex: null,
  branchTotal: null,
  checkpoint_id: null,
  moldyBranchPickerDisplayOnly: null,
  moldySuppressBranchPicker: true,
} as const

export function mergeServerMessageMetadata(
  messages: readonly BaseMessage[],
  metadataSnapshot: ServerMessageMetadataSnapshot,
): BaseMessage[] {
  if (
    metadataSnapshot.byId.size === 0 &&
    metadataSnapshot.byIndex.length === 0 &&
    metadataSnapshot.idByIndex.length === 0
  ) {
    return [...messages]
  }
  return messages.map((message, index) => {
    const messageId = message.id
    const metadata =
      (messageId ? metadataSnapshot.byId.get(messageId) : undefined) ??
      metadataSnapshot.byIndex[index]
    const replacementId = replacementMessageId(message, metadataSnapshot.idByIndex[index])
    if (!metadata && !replacementId) return message
    return withAdditionalMessageMetadata(message, metadata ?? {}, replacementId)
  })
}

export function applyPendingEditBranchMetadata(
  messages: readonly BaseMessage[],
  pendingEdit: PendingEditRenderState | null,
): readonly BaseMessage[] {
  if (!pendingEdit) return messages
  const targetIndex = pendingEditTargetIndex(messages, pendingEdit)
  if (targetIndex < 0) return messages
  const targetMessage = messages[targetIndex]
  if (!targetMessage) return messages
  if (!messageContentEqualsText(targetMessage, pendingEdit.content)) return messages
  const replacement = cloneMessageWithPendingEditBranchMetadata(targetMessage, pendingEdit)
  if (!replacement || replacement === targetMessage) return messages
  return [...messages.slice(0, targetIndex), replacement, ...messages.slice(targetIndex + 1)]
}

export function applyPendingReloadBranchMetadata(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState | null,
): readonly BaseMessage[] {
  if (!pendingReload) return messages
  const targetIndex = pendingReloadBranchMetadataTargetIndex(messages, pendingReload)
  if (targetIndex < 0) return messages
  const targetMessage = messages[targetIndex]
  if (!isAssistantMessage(targetMessage)) return messages
  const replacement = cloneMessageWithPendingReloadBranchMetadata(targetMessage, pendingReload)
  if (replacement === targetMessage) return messages
  return [...messages.slice(0, targetIndex), replacement, ...messages.slice(targetIndex + 1)]
}

export function cloneMessageWithPendingReloadBranchMetadata(
  message: BaseMessage,
  pendingReload: PendingReloadRenderState,
): BaseMessage {
  const branchTotal = pendingReload.requiredAssistantBranch.branchTotal + 1
  const branchIndex = branchTotal - 1
  const additionalKwargs = isRecord(message.additional_kwargs) ? message.additional_kwargs : {}
  const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  const clone = Object.create(Object.getPrototypeOf(message)) as BaseMessage
  Object.assign(clone, message, {
    additional_kwargs: {
      ...additionalKwargs,
      metadata: {
        ...withoutBranchPickerCustomMetadata(metadata),
        branches: Array.from({ length: branchTotal }, (_, index) => `pending-reload-${index}`),
        siblingCheckpointIds: Array.from(
          { length: branchTotal },
          (_, index) => `pending-reload-${index}`,
        ),
        activeBranchId: `pending-reload-${branchIndex}`,
        branchCheckpointId: `pending-reload-${branchIndex}`,
        branchIndex,
        branchTotal,
        checkpoint_id: `pending-reload-${branchIndex}`,
        moldyBranchPickerDisplayOnly: true,
      },
    },
  })
  return clone
}

export function cloneMessageWithPendingEditBranchMetadata(
  message: BaseMessage,
  pendingEdit: PendingEditRenderState,
): BaseMessage | undefined {
  const additionalKwargs = isRecord(message.additional_kwargs) ? message.additional_kwargs : {}
  const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  const nextMetadata =
    pendingEdit.pendingBranchTotal !== null
      ? pendingBranchPickerCustomMetadata(metadata, pendingEdit.pendingBranchTotal, 'pending-edit')
      : clearedBranchPickerCustomMetadata(metadata)
  const nextAdditionalKwargs = {
    ...additionalKwargs,
    metadata: nextMetadata,
  }
  if (stableString(nextAdditionalKwargs) === stableString(message.additional_kwargs)) return message
  const clone = Object.create(Object.getPrototypeOf(message)) as BaseMessage
  Object.assign(clone, message, {
    additional_kwargs: nextAdditionalKwargs,
  })
  return clone
}

export function replacementMessageId(
  message: BaseMessage,
  candidate: string | null | undefined,
): string | null {
  if (!candidate) return null
  const current = message.id
  if (typeof current !== 'string' || current.length === 0) return candidate
  if (current.startsWith('opt-') || current.startsWith('stream-')) return candidate
  return null
}

export function withAdditionalMessageMetadata(
  message: BaseMessage,
  metadata: Record<string, unknown>,
  replacementId: string | null = null,
): BaseMessage {
  const additionalKwargs = isRecord(message.additional_kwargs) ? message.additional_kwargs : {}
  const existingMetadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  const clone = Object.create(Object.getPrototypeOf(message)) as BaseMessage
  Object.assign(clone, message, {
    ...(replacementId ? { id: replacementId } : {}),
    additional_kwargs: {
      ...additionalKwargs,
      metadata: {
        ...existingMetadata,
        ...metadata,
      },
    },
  })
  return clone
}

export function appendTerminalRunNotice(
  messages: readonly BaseMessage[],
  notice: ThreadRunNotice | null,
  text: string,
): BaseMessage[] {
  if (!notice) return [...messages]
  const id = `moldy-${notice.status}-${notice.id}`
  if (messages.some((message) => message.id === id)) return [...messages]
  const nextMessages = [...messages]
  const precedingAssistantIndex = nextMessages.findLastIndex(isAssistantMessage)
  if (precedingAssistantIndex >= 0) {
    const precedingAssistant = nextMessages[precedingAssistantIndex]
    if (precedingAssistant) {
      nextMessages[precedingAssistantIndex] = withAdditionalMessageMetadata(precedingAssistant, {
        [TERMINAL_NOTICE_BOUNDARY_METADATA_KEY]: true,
      })
    }
  }
  return [
    ...nextMessages,
    new AIMessage({
      id,
      content: text,
      additional_kwargs: {
        metadata: {
          [TERMINAL_NOTICE_METADATA_KEY]: notice.status,
        },
      },
    }),
  ]
}
export function withoutBranchPickerCustomMetadata(value: unknown): Record<string, unknown> {
  const custom = isRecord(value) ? value : {}
  const remaining = { ...custom }
  for (const key of BRANCH_PICKER_METADATA_KEYS) {
    delete remaining[key]
  }
  return remaining
}

export function clearedBranchPickerCustomMetadata(value: unknown): Record<string, unknown> {
  return {
    ...withoutBranchPickerCustomMetadata(value),
    ...CLEARED_BRANCH_PICKER_METADATA,
  }
}

export function pendingBranchPickerCustomMetadata(
  value: unknown,
  branchTotal: number,
  idPrefix: string,
): Record<string, unknown> {
  const branchIndex = branchTotal - 1
  const branchIds = Array.from({ length: branchTotal }, (_, index) => `${idPrefix}-${index}`)
  return {
    ...withoutBranchPickerCustomMetadata(value),
    branches: branchIds,
    siblingCheckpointIds: branchIds,
    activeBranchId: branchIds[branchIndex] ?? null,
    branchCheckpointId: branchIds[branchIndex] ?? null,
    branchIndex,
    branchTotal,
    checkpoint_id: branchIds[branchIndex] ?? null,
    moldyBranchPickerDisplayOnly: true,
  }
}
