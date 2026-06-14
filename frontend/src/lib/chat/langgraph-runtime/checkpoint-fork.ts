import { useSyncExternalStore } from 'react'
import { STREAM_CONTROLLER, type MessageMetadataMap, type UseStreamReturn } from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
import type { AppendMessage, ThreadMessage } from '@assistant-ui/react'

type VisibleMessage = Pick<ThreadMessage, 'id'>
type CheckpointContext = {
  visibleMessages: readonly VisibleMessage[]
  metadataByMessageId: MessageMetadataMap
  checkpointByMessageId: ReadonlyMap<string, string>
  messageIdsByIndex?: readonly (readonly string[])[]
}

export function useMessageMetadataSnapshot<StateType extends object>(
  stream: UseStreamReturn<StateType>,
): MessageMetadataMap {
  const store = stream[STREAM_CONTROLLER].messageMetadataStore
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)
}

export function checkpointByMessageIdFromMessages(
  messages: readonly BaseMessage[],
): ReadonlyMap<string, string> {
  const checkpoints = new Map<string, string>()
  for (const message of messages) {
    const checkpointId = checkpointIdFromMessage(message)
    if (message.id && checkpointId) checkpoints.set(message.id, checkpointId)
  }
  return checkpoints
}

export function checkpointForEdit(
  message: Pick<AppendMessage, 'sourceId' | 'parentId'>,
  context: CheckpointContext,
): string | null {
  const visibleCandidateIds = uniquePresentIds([message.sourceId, message.parentId])
  const stableMessageIds = visibleCandidateIds.filter(isStableMessageId)
  if (visibleCandidateIds.length === 0) return null

  for (const messageId of stableMessageIds) {
    const liveCheckpoint = context.metadataByMessageId.get(messageId)?.parentCheckpointId
    if (liveCheckpoint) return liveCheckpoint
  }

  for (const messageId of visibleCandidateIds) {
    const previousId = previousVisibleMessageId(context.visibleMessages, messageId)
    if (!previousId) continue
    const checkpointId = context.checkpointByMessageId.get(previousId)
    if (checkpointId) return checkpointId
  }

  for (const messageId of visibleCandidateIds) {
    const index = visibleMessageIndex(context.visibleMessages, messageId)
    if (index < 0) continue
    const checkpointId = parentCheckpointForServerIndex(context, index)
    if (checkpointId) return checkpointId
  }

  return null
}

export function checkpointForReload(
  parentId: string | null,
  context: CheckpointContext,
): string | null {
  const targetId = nextVisibleMessageId(context.visibleMessages, parentId)
  const liveCheckpoint = targetId
    ? context.metadataByMessageId.get(targetId)?.parentCheckpointId
    : undefined
  if (liveCheckpoint) return liveCheckpoint

  if (targetId) {
    const targetIndex = visibleMessageIndex(context.visibleMessages, targetId)
    const serverCheckpoint = parentCheckpointForServerIndex(context, targetIndex)
    if (serverCheckpoint) return serverCheckpoint
  }

  if (!parentId) return null
  const checkpointId = context.checkpointByMessageId.get(parentId)
  if (checkpointId) return checkpointId

  const parentIndex = visibleMessageIndex(context.visibleMessages, parentId)
  return checkpointForServerIndex(context, parentIndex)
}

function checkpointIdFromMessage(message: BaseMessage): string | null {
  const metadata = message.additional_kwargs.metadata
  if (!isRecord(metadata)) return null
  const checkpointId = metadata.checkpoint_id
  return typeof checkpointId === 'string' && checkpointId ? checkpointId : null
}

function previousVisibleMessageId(
  messages: readonly VisibleMessage[],
  messageId: string,
): string | null {
  const index = messages.findIndex((message) => message.id === messageId)
  if (index <= 0) return null
  return messages[index - 1]?.id ?? null
}

function nextVisibleMessageId(
  messages: readonly VisibleMessage[],
  parentId: string | null,
): string | null {
  if (parentId === null) return messages[0]?.id ?? null
  const index = messages.findIndex((message) => message.id === parentId)
  if (index < 0) return null
  return messages[index + 1]?.id ?? null
}

function visibleMessageIndex(messages: readonly VisibleMessage[], messageId: string): number {
  return messages.findIndex((message) => message.id === messageId)
}

function parentCheckpointForServerIndex(context: CheckpointContext, index: number): string | null {
  if (index < 0) return null
  const serverIds = context.messageIdsByIndex?.[index] ?? []
  for (const serverId of serverIds) {
    const checkpointId = context.metadataByMessageId.get(serverId)?.parentCheckpointId
    if (checkpointId) return checkpointId
  }
  return null
}

function checkpointForServerIndex(context: CheckpointContext, index: number): string | null {
  if (index < 0) return null
  const serverIds = context.messageIdsByIndex?.[index] ?? []
  for (const serverId of serverIds) {
    const checkpointId = context.checkpointByMessageId.get(serverId)
    if (checkpointId) return checkpointId
  }
  return null
}

function isStableMessageId(value: string): boolean {
  return !value.startsWith('opt-') && !value.startsWith('stream-')
}

function uniquePresentIds(values: readonly (string | null | undefined)[]): string[] {
  const ids: string[] = []
  for (const value of values) {
    if (value && !ids.includes(value)) ids.push(value)
  }
  return ids
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
