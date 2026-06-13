import { useSyncExternalStore } from 'react'
import { STREAM_CONTROLLER, type MessageMetadataMap, type UseStreamReturn } from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
import type { AppendMessage, ThreadMessage } from '@assistant-ui/react'

type VisibleMessage = Pick<ThreadMessage, 'id'>

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
  context: {
    visibleMessages: readonly VisibleMessage[]
    metadataByMessageId: MessageMetadataMap
    checkpointByMessageId: ReadonlyMap<string, string>
  },
): string | null {
  const sourceId = stableMessageId(message.sourceId) ?? stableMessageId(message.parentId)
  if (!sourceId) return null

  const liveCheckpoint = context.metadataByMessageId.get(sourceId)?.parentCheckpointId
  if (liveCheckpoint) return liveCheckpoint

  const previousId = previousVisibleMessageId(context.visibleMessages, sourceId)
  if (!previousId) return null
  return context.checkpointByMessageId.get(previousId) ?? null
}

export function checkpointForReload(
  parentId: string | null,
  context: {
    visibleMessages: readonly VisibleMessage[]
    metadataByMessageId: MessageMetadataMap
    checkpointByMessageId: ReadonlyMap<string, string>
  },
): string | null {
  const targetId = nextVisibleMessageId(context.visibleMessages, parentId)
  const liveCheckpoint = targetId
    ? context.metadataByMessageId.get(targetId)?.parentCheckpointId
    : undefined
  if (liveCheckpoint) return liveCheckpoint

  if (!parentId) return null
  return context.checkpointByMessageId.get(parentId) ?? null
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

function stableMessageId(value: string | null | undefined): string | null {
  return value && !value.startsWith('opt-') && !value.startsWith('stream-') ? value : null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
