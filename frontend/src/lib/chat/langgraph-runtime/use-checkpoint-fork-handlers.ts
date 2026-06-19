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
import { reportClientWarning } from '@/lib/logging/client-logger'

const CHECKPOINT_CONTEXT_RETRY_INTERVAL_MS = 250
const CHECKPOINT_CONTEXT_RETRY_TIMEOUT_MS = 10_000

interface UseCheckpointForkHandlersOptions<StateType extends object> {
  conversationId: string
  stream: UseStreamReturn<StateType>
  visibleMessages: readonly Pick<ThreadMessage, 'id'>[]
  langChainMessages: readonly BaseMessage[]
  onBeforeEditSubmit?: (edit: PendingCheckpointEditSubmit) => void
}

type SubmitInput<StateType extends object> = Parameters<UseStreamReturn<StateType>['submit']>[0]

interface ServerCheckpointAttempt {
  readonly checkpointId: string | null
  readonly hasServerMessages: boolean
}

export interface PendingCheckpointEditSubmit {
  readonly content: string
  readonly parentId: string | null
  readonly sourceId: string | null
  readonly targetId: string | null
  readonly targetIndex: number | null
}

export function useCheckpointForkHandlers<StateType extends object>({
  conversationId,
  stream,
  visibleMessages,
  langChainMessages,
  onBeforeEditSubmit,
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
      if (!content && attachments.length === 0) return false

      const checkpointId =
        checkpointForEdit(message, checkpointContext) ??
        (await checkpointForEditFromServer(message, conversationId, checkpointContext))
      if (!checkpointId) {
        reportClientWarning('useMoldyLangGraphStream', 'Edit skipped: checkpoint is unavailable.')
        return false
      }
      const target = pendingEditVisibleTarget(message, checkpointContext.visibleMessages)
      onBeforeEditSubmit?.({
        content,
        parentId: message.parentId ?? null,
        sourceId: message.sourceId ?? null,
        targetId: target.id,
        targetIndex: target.index,
      })
      await stream.submit(
        humanInput<StateType>(
          content,
          attachments,
          message.sourceId ?? message.parentId ?? undefined,
        ),
        {
          forkFrom: checkpointId,
        },
      )
      return true
    },
    [checkpointContext, conversationId, onBeforeEditSubmit, stream],
  )

  const onReload = useCallback(
    async (parentId: string | null) => {
      const checkpointId =
        checkpointForReload(parentId, checkpointContext) ??
        (await checkpointForReloadFromServer(parentId, conversationId, checkpointContext))
      if (!checkpointId) {
        reportClientWarning('useMoldyLangGraphStream', 'Reload skipped: checkpoint is unavailable.')
        return false
      }
      await stream.submit(null, { forkFrom: checkpointId })
      return true
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
  id?: string,
): SubmitInput<StateType> {
  return {
    messages: [new HumanMessage({ content, ...(id ? { id } : {}) })],
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

function pendingEditVisibleTarget(
  message: Pick<AppendMessage, 'sourceId' | 'parentId'>,
  visibleMessages: readonly Pick<ThreadMessage, 'id'>[],
): { readonly id: string | null; readonly index: number | null } {
  const candidateIds = uniquePresentIds([message.sourceId, message.parentId])
  for (const candidateId of candidateIds) {
    const index = visibleMessages.findIndex((visibleMessage) => visibleMessage.id === candidateId)
    if (index >= 0) return { id: candidateId, index }
  }
  return { id: null, index: null }
}

function uniquePresentIds(values: readonly (string | null | undefined)[]): string[] {
  const ids: string[] = []
  for (const value of values) {
    if (value && !ids.includes(value)) ids.push(value)
  }
  return ids
}

type CheckpointContext = Parameters<typeof checkpointForEdit>[1]

async function checkpointForEditFromServer(
  message: Pick<AppendMessage, 'sourceId' | 'parentId'>,
  conversationId: string,
  context: CheckpointContext,
): Promise<string | null> {
  return retryServerCheckpoint(() =>
    checkpointForEditFromServerOnce(message, conversationId, context),
  )
}

async function checkpointForReloadFromServer(
  parentId: string | null,
  conversationId: string,
  context: CheckpointContext,
): Promise<string | null> {
  return retryServerCheckpoint(() =>
    checkpointForReloadFromServerOnce(parentId, conversationId, context),
  )
}

async function checkpointForEditFromServerOnce(
  message: Pick<AppendMessage, 'sourceId' | 'parentId'>,
  conversationId: string,
  context: CheckpointContext,
): Promise<ServerCheckpointAttempt | null> {
  const serverContext = await loadServerCheckpointContext(conversationId).catch(() => null)
  if (!serverContext) return null
  return {
    checkpointId: checkpointForEdit(message, mergeCheckpointContext(context, serverContext)),
    hasServerMessages: serverContext.messageIdsByIndex.length > 0,
  }
}

async function checkpointForReloadFromServerOnce(
  parentId: string | null,
  conversationId: string,
  context: CheckpointContext,
): Promise<ServerCheckpointAttempt | null> {
  const serverContext = await loadServerCheckpointContext(conversationId).catch(() => null)
  if (!serverContext) return null
  return {
    checkpointId: checkpointForReload(parentId, mergeCheckpointContext(context, serverContext)),
    hasServerMessages: serverContext.messageIdsByIndex.length > 0,
  }
}

async function retryServerCheckpoint(
  resolve: () => Promise<ServerCheckpointAttempt | null>,
): Promise<string | null> {
  const startedAt = Date.now()
  while (Date.now() - startedAt <= CHECKPOINT_CONTEXT_RETRY_TIMEOUT_MS) {
    const attempt = await resolve()
    if (attempt?.checkpointId) return attempt.checkpointId
    if (attempt?.hasServerMessages) return null
    await sleep(CHECKPOINT_CONTEXT_RETRY_INTERVAL_MS)
  }
  return null
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
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
