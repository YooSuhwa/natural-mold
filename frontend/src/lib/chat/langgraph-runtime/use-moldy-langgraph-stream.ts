'use client'

import { useCallback, useMemo } from 'react'
import {
  useExternalMessageConverter,
  useExternalStoreRuntime,
  type AttachmentAdapter,
  type FeedbackAdapter,
} from '@assistant-ui/react'
import { useChannel, useStream, type Channel } from '@langchain/react'
import { HumanMessage, type BaseMessage } from '@langchain/core/messages'
import { convertLangChainBaseMessage } from '@assistant-ui/react-langchain'
import { reduceProtocolActivity } from './activity-protocol'
import { createMoldyAgentTransport } from './moldy-agent-transport'
import type { RunActivity } from './activity-model'

interface MoldyGraphState {
  messages: BaseMessage[]
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

const ACTIVITY_CHANNELS = [
  'messages',
  'tools',
  'values',
  'updates',
  'lifecycle',
  'tasks',
  'checkpoints',
  'custom',
] as const satisfies readonly Channel[]

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
      return 'text' in part && typeof part.text === 'string' ? part.text : ''
    })
    .join('')
}

function convertMoldyLangChainMessage(
  message: BaseMessage,
  metadata: Parameters<typeof convertLangChainBaseMessage>[1],
) {
  return convertLangChainBaseMessage(message, metadata)
}

export function useMoldyLangGraphStream({
  agentId,
  conversationId,
  feedbackAdapter,
  attachmentAdapter,
}: UseMoldyLangGraphStreamOptions) {
  const transport = useMemo(
    () => createMoldyAgentTransport(conversationId, agentId),
    [agentId, conversationId],
  )
  const stream = useStream<MoldyGraphState>({
    transport,
    threadId: conversationId,
  })
  const activityEvents = useChannel(stream, ACTIVITY_CHANNELS, undefined, { bufferSize: 300 })
  const activities = useMemo(
    () =>
      activityEvents.reduce<RunActivity[]>(
        (current, event) => reduceProtocolActivity(current, event),
        [],
      ),
    [activityEvents],
  )
  const messages = useExternalMessageConverter({
    callback: convertMoldyLangChainMessage,
    messages: stream.messages,
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
      await stream.submit({ messages: [new HumanMessage(content)] })
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

  const sendMessage = useCallback(
    async (content: string) => {
      const trimmed = content.trim()
      if (!trimmed) return
      await stream.submit({ messages: [new HumanMessage(trimmed)] })
    },
    [stream],
  )
  const onResumeDecisions = useCallback(async () => {}, [])
  const registerDecision = useCallback(async () => {}, [])

  return {
    stream,
    assistantRuntime,
    activities,
    sendMessage,
    onResumeDecisions,
    registerDecision,
  }
}
