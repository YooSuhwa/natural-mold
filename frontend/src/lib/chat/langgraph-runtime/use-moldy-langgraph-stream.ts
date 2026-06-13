'use client'

import { useCallback, useEffect, useMemo, useRef } from 'react'
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
import { useLangGraphArtifactEffects } from './artifact-events'
import { selectDeepAgentsState } from './deepagents-state'
import { appendInterruptToolCallMessages, standardPayloadsFromInterrupts } from './hitl-interrupts'
import { useLangGraphMemoryEffects } from './memory-events'
import { createMoldyAgentTransport } from './moldy-agent-transport'
import { useLangGraphUsageEffects } from './usage-events'
import type { RunActivity } from './activity-model'
import { createHiTLDecisionCoordinator, type HiTLDecisionCoordinator } from '../standard-interrupt'
import type { Decision } from '@/lib/types'

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

function attachmentRefs(message: { attachments?: readonly { id?: unknown }[] }): { id: string }[] {
  return (
    message.attachments
      ?.map((attachment) => (typeof attachment.id === 'string' ? { id: attachment.id } : null))
      .filter((attachment): attachment is { id: string } => attachment !== null) ?? []
  )
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
  const deepAgentsState = useMemo(() => selectDeepAgentsState(stream.values ?? {}), [stream.values])
  const interruptPayloads = useMemo(
    () => standardPayloadsFromInterrupts(stream.interrupts),
    [stream.interrupts],
  )
  const interruptPayloadsById = useMemo(
    () => new Map(interruptPayloads.map((payload) => [payload.interrupt_id, payload])),
    [interruptPayloads],
  )
  const messagesWithInterrupts = useMemo(
    () => appendInterruptToolCallMessages(stream.messages, interruptPayloads),
    [stream.messages, interruptPayloads],
  )
  const messagesWithArtifacts = useLangGraphArtifactEffects({
    stream,
    conversationId,
    messages: messagesWithInterrupts,
  })
  const messagesWithUsage = useLangGraphUsageEffects({
    stream,
    messages: messagesWithArtifacts,
  })
  useLangGraphMemoryEffects({ stream })
  const messages = useExternalMessageConverter({
    callback: convertMoldyLangChainMessage,
    messages: messagesWithUsage,
    isRunning: stream.isLoading,
  })
  const coordinatorsRef = useRef(new Map<string, HiTLDecisionCoordinator>())
  useEffect(() => {
    const active = new Set(interruptPayloads.map((payload) => payload.interrupt_id))
    for (const key of coordinatorsRef.current.keys()) {
      if (!active.has(key)) coordinatorsRef.current.delete(key)
    }
  }, [interruptPayloads])
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
      attachments?: readonly { id?: unknown; content?: readonly unknown[] }[]
    }) => {
      const content = appendMessageText(message).trim()
      const attachments = attachmentRefs(message)
      if (!content && attachments.length === 0) return
      await stream.submit({
        messages: [new HumanMessage(content)],
        ...(attachments.length > 0 ? { attachments } : {}),
      })
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
  const firstInterruptId = interruptPayloads[0]?.interrupt_id ?? null
  const onResumeDecisions = useCallback(
    async (decisions: Decision[], _displayText?: string, interruptId?: string | null) => {
      const targetId = interruptId ?? firstInterruptId
      const response = { decisions }
      if (targetId) {
        await stream.respond(response, { interruptId: targetId })
        return
      }
      await stream.respond(response)
    },
    [firstInterruptId, stream],
  )
  const registerDecision = useCallback(
    async (
      actionIndex: number,
      decision: Decision,
      displayText?: string,
      interruptId?: string | null,
    ) => {
      const targetId = interruptId ?? firstInterruptId
      const payload = targetId ? interruptPayloadsById.get(targetId) : interruptPayloads[0]
      if (!targetId || !payload || payload.action_requests.length <= 1) {
        await onResumeDecisions([decision], displayText, targetId)
        return
      }
      const existing = coordinatorsRef.current.get(targetId)
      const coordinator =
        existing ??
        createHiTLDecisionCoordinator({
          totalActions: payload.action_requests.length,
          interruptId: targetId,
          resume: onResumeDecisions,
        })
      coordinatorsRef.current.set(targetId, coordinator)
      await coordinator.registerDecision(actionIndex, decision, displayText)
    },
    [firstInterruptId, interruptPayloads, interruptPayloadsById, onResumeDecisions],
  )

  return {
    stream,
    assistantRuntime,
    activities,
    deepAgentsState,
    sendMessage,
    onResumeDecisions,
    registerDecision,
  }
}
