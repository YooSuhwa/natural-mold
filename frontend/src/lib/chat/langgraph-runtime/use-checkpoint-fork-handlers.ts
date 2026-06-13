import { useCallback, useMemo } from 'react'
import { HumanMessage, type BaseMessage } from '@langchain/core/messages'
import type { UseStreamReturn } from '@langchain/react'
import type { AppendMessage, ThreadMessage } from '@assistant-ui/react'
import {
  checkpointByMessageIdFromMessages,
  checkpointForEdit,
  checkpointForReload,
  useMessageMetadataSnapshot,
} from './checkpoint-fork'

interface UseCheckpointForkHandlersOptions<StateType extends object> {
  stream: UseStreamReturn<StateType>
  visibleMessages: readonly Pick<ThreadMessage, 'id'>[]
  langChainMessages: readonly BaseMessage[]
}

type SubmitInput<StateType extends object> = Parameters<UseStreamReturn<StateType>['submit']>[0]

export function useCheckpointForkHandlers<StateType extends object>({
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

      const checkpointId = checkpointForEdit(message, checkpointContext)
      if (!checkpointId) {
        console.warn('[useMoldyLangGraphStream] Edit skipped: checkpoint is unavailable.')
        return
      }
      await stream.submit(humanInput<StateType>(content, attachments), { forkFrom: checkpointId })
    },
    [checkpointContext, stream],
  )

  const onReload = useCallback(
    async (parentId: string | null) => {
      const checkpointId = checkpointForReload(parentId, checkpointContext)
      if (!checkpointId) {
        console.warn('[useMoldyLangGraphStream] Reload skipped: checkpoint is unavailable.')
        return
      }
      await stream.submit(null, { forkFrom: checkpointId })
    },
    [checkpointContext, stream],
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
