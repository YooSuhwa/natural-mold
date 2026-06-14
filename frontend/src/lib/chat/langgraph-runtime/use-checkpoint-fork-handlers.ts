import { useCallback, useMemo } from 'react'
import { HumanMessage, type BaseMessage } from '@langchain/core/messages'
import type { MessageMetadata, MessageMetadataMap, UseStreamReturn } from '@langchain/react'
import type { AppendMessage, ThreadMessage } from '@assistant-ui/react'
import {
  checkpointByMessageIdFromMessages,
  checkpointForEdit,
  checkpointForReload,
  useMessageMetadataSnapshot,
} from './checkpoint-fork'
import {
  loadServerCheckpointContext,
  type ServerCheckpointContext,
} from './thread-state-checkpoints'

interface UseCheckpointForkHandlersOptions<StateType extends object> {
  conversationId: string
  stream: UseStreamReturn<StateType>
  visibleMessages: readonly Pick<ThreadMessage, 'id'>[]
  langChainMessages: readonly BaseMessage[]
}

type SubmitInput<StateType extends object> = Parameters<UseStreamReturn<StateType>['submit']>[0]

export function useCheckpointForkHandlers<StateType extends object>({
  conversationId,
  stream,
  visibleMessages,
  langChainMessages,
}: UseCheckpointForkHandlersOptions<StateType>) {
  const metadataByMessageId = useMessageMetadataSnapshot(stream)
  const checkpointByMessageId = useMemo(
    () => checkpointByMessageIdFromMessages(langChainMessages),
    [langChainMessages],
  )
  const checkpointContext = useMemo(
    () => ({
      visibleMessages,
      metadataByMessageId,
      checkpointByMessageId,
    }),
    [visibleMessages, metadataByMessageId, checkpointByMessageId],
  )

  const onNew = useCallback(
    async (message: AppendMessage) => {
      const content = appendMessageText(message).trim()
      const attachments = attachmentRefs(message)
      if (!content && attachments.length === 0) return
      await stream.submit(humanInput<StateType>(content, attachments))
    },
    [stream],
  )

  const onEdit = useCallback(
    async (message: AppendMessage) => {
      const content = appendMessageText(message).trim()
      const attachments = attachmentRefs(message)
      if (!content && attachments.length === 0) return

      const checkpointId =
        checkpointForEdit(message, checkpointContext) ??
        (await checkpointForEditFromServer(message, conversationId, checkpointContext))
      if (!checkpointId) {
        console.warn('[useMoldyLangGraphStream] Edit skipped: checkpoint is unavailable.')
        return
      }
      await stream.submit(humanInput<StateType>(content, attachments), { forkFrom: checkpointId })
    },
    [checkpointContext, conversationId, stream],
  )

  const onReload = useCallback(
    async (parentId: string | null) => {
      const checkpointId =
        checkpointForReload(parentId, checkpointContext) ??
        (await checkpointForReloadFromServer(parentId, conversationId, checkpointContext))
      if (!checkpointId) {
        console.warn('[useMoldyLangGraphStream] Reload skipped: checkpoint is unavailable.')
        return
      }
      await stream.submit(null, { forkFrom: checkpointId })
    },
    [checkpointContext, conversationId, stream],
  )

  return { onNew, onEdit, onReload }
}

function appendMessageText(message: {
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
      if (typeof part !== 'object' || part === null) return ''
      return 'text' in part && typeof part.text === 'string' ? part.text : ''
    })
    .join('')
}

function humanInput<StateType extends object>(
  content: string,
  attachments: readonly { id: string }[],
): SubmitInput<StateType> {
  return {
    messages: [new HumanMessage(content)],
    ...(attachments.length > 0 ? { attachments } : {}),
  } as unknown as SubmitInput<StateType>
}

function attachmentRefs(message: { attachments?: readonly { id?: unknown }[] }): { id: string }[] {
  return (
    message.attachments
      ?.map((attachment) => (typeof attachment.id === 'string' ? { id: attachment.id } : null))
      .filter((attachment): attachment is { id: string } => attachment !== null) ?? []
  )
}

type CheckpointContext = Parameters<typeof checkpointForEdit>[1]

async function checkpointForEditFromServer(
  message: Pick<AppendMessage, 'sourceId' | 'parentId'>,
  conversationId: string,
  context: CheckpointContext,
): Promise<string | null> {
  const serverContext = await loadServerCheckpointContext(conversationId).catch(() => null)
  if (!serverContext) return null
  return checkpointForEdit(message, mergeCheckpointContext(context, serverContext))
}

async function checkpointForReloadFromServer(
  parentId: string | null,
  conversationId: string,
  context: CheckpointContext,
): Promise<string | null> {
  const serverContext = await loadServerCheckpointContext(conversationId).catch(() => null)
  if (!serverContext) return null
  return checkpointForReload(parentId, mergeCheckpointContext(context, serverContext))
}

function mergeCheckpointContext(
  local: CheckpointContext,
  server: ServerCheckpointContext,
): CheckpointContext {
  return {
    visibleMessages: local.visibleMessages,
    metadataByMessageId: mergeMetadataMaps(server.metadataByMessageId, local.metadataByMessageId),
    checkpointByMessageId: mergeMaps(server.checkpointByMessageId, local.checkpointByMessageId),
    messageIdsByIndex: server.messageIdsByIndex,
  }
}

function mergeMetadataMaps(
  fallback: MessageMetadataMap,
  primary: MessageMetadataMap,
): MessageMetadataMap {
  if (fallback.size === 0) return primary
  if (primary.size === 0) return fallback
  const merged = new Map<string, MessageMetadata>(fallback)
  for (const [key, metadata] of primary) {
    merged.set(key, mergeMessageMetadata(fallback.get(key), metadata))
  }
  return merged
}

function mergeMessageMetadata(
  fallback: MessageMetadata | undefined,
  primary: MessageMetadata,
): MessageMetadata {
  return {
    parentCheckpointId: primary.parentCheckpointId ?? fallback?.parentCheckpointId,
    ...(primary.optimisticStatus !== undefined
      ? { optimisticStatus: primary.optimisticStatus }
      : fallback?.optimisticStatus !== undefined
        ? { optimisticStatus: fallback.optimisticStatus }
        : {}),
  }
}

function mergeMaps<K, V>(
  fallback: ReadonlyMap<K, V>,
  primary: ReadonlyMap<K, V>,
): ReadonlyMap<K, V> {
  if (fallback.size === 0) return primary
  if (primary.size === 0) return fallback
  return new Map([...fallback, ...primary])
}
