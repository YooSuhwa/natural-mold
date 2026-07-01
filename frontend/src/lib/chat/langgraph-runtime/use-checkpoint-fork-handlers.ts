import { useCallback, useEffect, useMemo, useRef } from 'react'
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
import { sourceMessageIdFromThreadMessageId } from './message-list'
import { isTerminalNoticeMessageId } from './terminal-notice'
import { reportClientWarning } from '@/lib/logging/client-logger'

const CHECKPOINT_CONTEXT_RETRY_INTERVAL_MS = 250
const CHECKPOINT_CONTEXT_RETRY_TIMEOUT_MS = 10_000

interface UseCheckpointForkHandlersOptions<StateType extends object> {
  conversationId: string
  stream: UseStreamReturn<StateType>
  visibleMessages: readonly VisibleMessageReference[]
  langChainMessages: readonly BaseMessage[]
  onBeforeEditSubmit?: (edit: PendingCheckpointEditSubmit) => void
}

type VisibleMessageReference = Pick<ThreadMessage, 'id'> & {
  readonly sourceId?: string
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
  // 합성 terminal-notice 버블(실패/취소/stale)은 실제 assistant 턴이 아니므로
  // checkpoint fork 대상 탐색에서 제외한다. 특히 실패 버블을 남겨두면
  // checkpointForReload가 그것을 재생성 대상 assistant로 오인해 null로 끝나
  // retry가 no-op이 된다(G2).
  const forkVisibleMessages = useMemo(
    () => visibleMessages.filter((message) => !isTerminalNoticeMessageId(message.id)),
    [visibleMessages],
  )
  const checkpointContext = useMemo(
    () => ({
      visibleMessages: forkVisibleMessages,
      metadataByMessageId,
      checkpointByMessageId,
    }),
    [forkVisibleMessages, metadataByMessageId, checkpointByMessageId],
  )

  // 서버 checkpoint 폴링(최대 10s)을 unmount/handler 재생성 시 취소한다.
  // 취소가 없으면 dead stream에 ``stream.submit``을 호출할 수 있다.
  const serverCheckpointAbortRef = useRef<AbortController | null>(null)
  const beginServerCheckpointPoll = useCallback(() => {
    serverCheckpointAbortRef.current?.abort()
    const controller = new AbortController()
    serverCheckpointAbortRef.current = controller
    return controller.signal
  }, [])
  useEffect(() => {
    return () => {
      serverCheckpointAbortRef.current?.abort()
      serverCheckpointAbortRef.current = null
    }
  }, [])

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

      const localCheckpointId = checkpointForEdit(message, checkpointContext)
      let signal: AbortSignal | null = null
      const checkpointId =
        localCheckpointId ??
        (await checkpointForEditFromServer(
          message,
          conversationId,
          checkpointContext,
          (signal = beginServerCheckpointPoll()),
        ))
      // 서버 폴링을 거쳤고 그 사이 unmount/handler 재생성으로 취소됐다면
      // dead stream에 submit하지 않는다.
      if (signal?.aborted) return false
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
          sourceMessageIdForVisibleCandidate(
            message.sourceId ?? message.parentId,
            checkpointContext.visibleMessages,
          ) ?? undefined,
        ),
        {
          forkFrom: checkpointId,
        },
      )
      return true
    },
    [beginServerCheckpointPoll, checkpointContext, conversationId, onBeforeEditSubmit, stream],
  )

  const onReload = useCallback(
    async (parentId: string | null) => {
      const localCheckpointId = checkpointForReload(parentId, checkpointContext)
      let signal: AbortSignal | null = null
      const checkpointId =
        localCheckpointId ??
        (await checkpointForReloadFromServer(
          parentId,
          conversationId,
          checkpointContext,
          (signal = beginServerCheckpointPoll()),
        ))
      if (signal?.aborted) return false
      if (!checkpointId) {
        reportClientWarning('useMoldyLangGraphStream', 'Reload skipped: checkpoint is unavailable.')
        return false
      }
      await stream.submit(null, { forkFrom: checkpointId })
      return true
    },
    [beginServerCheckpointPoll, checkpointContext, conversationId, stream],
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
  visibleMessages: readonly VisibleMessageReference[],
): { readonly id: string | null; readonly index: number | null } {
  const candidateIds = uniquePresentIds([message.sourceId, message.parentId])
  for (const candidateId of candidateIds) {
    const index = visibleMessages.findIndex((visibleMessage) => visibleMessage.id === candidateId)
    if (index >= 0) return { id: candidateId, index }
  }
  return { id: null, index: null }
}

function sourceMessageIdForVisibleCandidate(
  messageId: string | null | undefined,
  visibleMessages: readonly VisibleMessageReference[],
): string | null {
  if (!messageId) return null
  const visibleMessage = visibleMessages.find((message) => message.id === messageId)
  return visibleMessage?.sourceId ?? sourceMessageIdFromThreadMessageId(messageId) ?? messageId
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
  signal: AbortSignal,
): Promise<string | null> {
  return retryServerCheckpoint(
    () => checkpointForEditFromServerOnce(message, conversationId, context),
    signal,
  )
}

async function checkpointForReloadFromServer(
  parentId: string | null,
  conversationId: string,
  context: CheckpointContext,
  signal: AbortSignal,
): Promise<string | null> {
  return retryServerCheckpoint(
    () => checkpointForReloadFromServerOnce(parentId, conversationId, context),
    signal,
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
  signal: AbortSignal,
): Promise<string | null> {
  const startedAt = Date.now()
  while (Date.now() - startedAt <= CHECKPOINT_CONTEXT_RETRY_TIMEOUT_MS) {
    if (signal.aborted) return null
    const attempt = await resolve()
    if (signal.aborted) return null
    if (attempt?.checkpointId) return attempt.checkpointId
    if (attempt?.hasServerMessages) return null
    await sleep(CHECKPOINT_CONTEXT_RETRY_INTERVAL_MS, signal)
    if (signal.aborted) return null
  }
  return null
}

/** abort 가능한 sleep — signal이 발화하면 즉시 resolve해 폴링 루프를 빠르게
 *  빠져나가게 한다(timer leak 방지 포함). */
function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve()
      return
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      resolve()
    }
    signal.addEventListener('abort', onAbort, { once: true })
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
