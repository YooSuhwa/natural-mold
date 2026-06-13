'use client'

import { useCallback, useMemo } from 'react'
import {
  useExternalMessageConverter,
  useExternalStoreRuntime,
  type AttachmentAdapter,
  type FeedbackAdapter,
} from '@assistant-ui/react'
import { useStream, type UseStreamReturn } from '@langchain/react'
import {
  convertLangChainBaseMessage,
  type LangChainBaseMessage,
} from '@assistant-ui/react-langchain'
import { createMoldyAgentTransport } from './moldy-agent-transport'
import type { RunActivity } from './activity-model'

interface MoldyGraphState {
  messages: LangChainBaseMessage[]
  todos?: unknown
  files?: unknown
  async_tasks?: unknown
}

interface UseMoldyLangGraphStreamOptions {
  agentId: string
  conversationId: string
  feedbackAdapter?: FeedbackAdapter
  attachmentAdapter?: AttachmentAdapter
}

function appendMessageText(message: {
  content: readonly unknown[]
  attachments?: readonly { content: readonly unknown[] }[]
}): string {
  const content = [
    ...message.content,
    ...(message.attachments?.flatMap((attachment) => attachment.content) ?? []),
  ]
  return content
    .map((part) => {
      if (typeof part === 'string') return part
      if (typeof part !== 'object' || part === null) return ''
      const text = (part as { text?: unknown }).text
      return typeof text === 'string' ? text : ''
    })
    .join('')
}

export function useMoldyLangGraphStream({
  agentId,
  conversationId,
  feedbackAdapter,
  attachmentAdapter,
}: UseMoldyLangGraphStreamOptions) {
  const transport = useMemo(() => createMoldyAgentTransport(conversationId), [conversationId])
  const stream = useStream<MoldyGraphState>({
    transport,
    threadId: conversationId,
    assistantId: agentId,
  })
  const messages = useExternalMessageConverter({
    callback: convertLangChainBaseMessage,
    messages: stream.messages as LangChainBaseMessage[],
    isRunning: stream.isLoading,
  })
  const adapters = useMemo(() => {
    if (!feedbackAdapter && !attachmentAdapter) return undefined
    return {
      ...(feedbackAdapter ? { feedback: feedbackAdapter } : {}),
      ...(attachmentAdapter ? { attachments: attachmentAdapter } : {}),
    }
  }, [feedbackAdapter, attachmentAdapter])
  const onNew = useCallback(
    async (message: {
      content: readonly unknown[]
      attachments?: readonly { content: readonly unknown[] }[]
    }) => {
      const content = appendMessageText(message).trim()
      if (!content) return
      await stream.submit({ messages: [{ type: 'human', content }] })
    },
    [stream],
  )
  const onCancel = useCallback(async () => {
    await stream.stop()
  }, [stream])

  const assistantRuntime = useExternalStoreRuntime({
    messages,
    isRunning: stream.isLoading,
    adapters,
    onNew,
    onCancel,
  })

  return {
    stream: stream as UseStreamReturn<MoldyGraphState>,
    assistantRuntime,
    activities: [] as readonly RunActivity[],
    sendMessage: async (content: string) => {
      const trimmed = content.trim()
      if (!trimmed) return
      await stream.submit({ messages: [{ type: 'human', content: trimmed }] })
    },
    onResumeDecisions: async () => {},
    registerDecision: async () => {},
  }
}
