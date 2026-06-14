'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  useExternalMessageConverter,
  useExternalStoreRuntime,
  type AttachmentAdapter,
  type FeedbackAdapter,
} from '@assistant-ui/react'
import { useChannel, useStream, type Channel } from '@langchain/react'
import { HumanMessage, type BaseMessage } from '@langchain/core/messages'
import { useSetAtom } from 'jotai'
import { reduceProtocolActivity } from './activity-protocol'
import { useLangGraphArtifactEffects } from './artifact-events'
import { selectDeepAgentsState } from './deepagents-state'
import {
  activeInterruptPayloads,
  appendInterruptToolCallMessages,
  appendResolvedInterruptToolCallMessages,
  resolvedInterruptToolCallsFromDecisions,
  standardPayloadsFromInterrupts,
  type LangGraphInterruptLike,
  type ResolvedInterruptToolCall,
} from './hitl-interrupts'
import { useLangGraphMemoryEffects } from './memory-events'
import { dedupeLangChainMessagesById, useStableConvertedMessages } from './message-list'
import { createMoldyAgentTransport } from './moldy-agent-transport'
import { refreshThreadLifecycleStream } from './lifecycle-subscription'
import { useCheckpointForkHandlers } from './use-checkpoint-fork-handlers'
import { useLangGraphUsageEffects } from './usage-events'
import { convertMoldyLangChainMessage } from './langchain-message-conversion'
import type { RunActivity } from './activity-model'
import { createHiTLDecisionCoordinator, type HiTLDecisionCoordinator } from '../standard-interrupt'
import { chatCancelInFlightAtom } from '@/lib/stores/chat-store'
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

function threadInterruptsFromStream(stream: {
  getThread: () => { readonly interrupts?: readonly LangGraphInterruptLike[] } | undefined
}): readonly LangGraphInterruptLike[] {
  return stream.getThread()?.interrupts ?? []
}

export function useMoldyLangGraphStream({
  agentId,
  conversationId,
  feedbackAdapter,
  attachmentAdapter,
}: UseMoldyLangGraphStreamOptions) {
  const setChatCancelInFlight = useSetAtom(chatCancelInFlightAtom)
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
  const [resolvedInterrupts, setResolvedInterrupts] = useState<ResolvedInterruptToolCall[]>([])
  const allInterruptPayloads = standardPayloadsFromInterrupts([
    ...stream.interrupts,
    ...threadInterruptsFromStream(stream),
  ])
  const interruptPayloads = useMemo(
    () => activeInterruptPayloads(allInterruptPayloads, stream.messages, resolvedInterrupts),
    [allInterruptPayloads, resolvedInterrupts, stream.messages],
  )
  const interruptPayloadsById = useMemo(
    () => new Map(interruptPayloads.map((payload) => [payload.interrupt_id, payload])),
    [interruptPayloads],
  )
  const allInterruptPayloadsById = useMemo(
    () => new Map(allInterruptPayloads.map((payload) => [payload.interrupt_id, payload])),
    [allInterruptPayloads],
  )
  const messagesWithInterrupts = useMemo(
    () =>
      dedupeLangChainMessagesById(
        appendResolvedInterruptToolCallMessages(
          appendInterruptToolCallMessages(stream.messages, interruptPayloads),
          resolvedInterrupts,
        ),
      ),
    [stream.messages, interruptPayloads, resolvedInterrupts],
  )
  const messagesWithArtifacts = useLangGraphArtifactEffects({
    stream,
    conversationId,
    messages: messagesWithInterrupts,
  })
  const messagesWithUsage = useLangGraphUsageEffects({
    stream,
    messages: messagesWithArtifacts,
    stateMessages: stream.values?.messages ?? [],
  })
  useLangGraphMemoryEffects({ stream })
  const isRunning = stream.isLoading && interruptPayloads.length === 0
  const convertedMessages = useExternalMessageConverter({
    callback: convertMoldyLangChainMessage,
    messages: messagesWithUsage,
    isRunning,
  })
  const messages = useStableConvertedMessages(convertedMessages, messagesWithUsage, isRunning)
  const { onNew, onEdit, onReload } = useCheckpointForkHandlers({
    stream,
    visibleMessages: messages,
    langChainMessages: messagesWithUsage,
  })
  const coordinatorsRef = useRef(new Map<string, HiTLDecisionCoordinator>())
  const pendingInterruptDecisionsRef = useRef(new Map<string, Decision[]>())
  useEffect(() => {
    const active = new Set(interruptPayloads.map((payload) => payload.interrupt_id))
    for (const key of coordinatorsRef.current.keys()) {
      if (!active.has(key)) coordinatorsRef.current.delete(key)
    }
    for (const key of pendingInterruptDecisionsRef.current.keys()) {
      if (!active.has(key)) pendingInterruptDecisionsRef.current.delete(key)
    }
  }, [interruptPayloads])
  const adapters = useMemo(() => {
    if (!feedbackAdapter && !attachmentAdapter) return undefined
    return {
      ...(feedbackAdapter ? { feedback: feedbackAdapter } : {}),
      ...(attachmentAdapter ? { attachments: attachmentAdapter } : {}),
    }
  }, [feedbackAdapter, attachmentAdapter])
  const onCancel = useCallback(async () => {
    setChatCancelInFlight(true)
    try {
      await stream.stop()
    } finally {
      setChatCancelInFlight(false)
    }
  }, [setChatCancelInFlight, stream])

  const rememberResolvedInterrupt = useCallback(
    (interruptId: string | null, decisions: readonly Decision[]) => {
      if (!interruptId) return
      const payload = allInterruptPayloadsById.get(interruptId)
      if (!payload) return
      const resolved = resolvedInterruptToolCallsFromDecisions(payload, decisions)
      if (resolved.length === 0) return
      const resolvedIds = new Set(resolved.map((item) => item.toolCall.id).filter(Boolean))
      setResolvedInterrupts((current) => [
        ...current.filter((item) => !item.toolCall.id || !resolvedIds.has(item.toolCall.id)),
        ...resolved,
      ])
    },
    [allInterruptPayloadsById],
  )

  const assistantRuntime = useExternalStoreRuntime({
    messages,
    isRunning,
    adapters,
    onNew,
    onEdit,
    onReload,
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
      const activeInterruptIds = interruptPayloads.map((payload) => payload.interrupt_id)
      if (targetId && activeInterruptIds.length > 1 && activeInterruptIds.includes(targetId)) {
        pendingInterruptDecisionsRef.current.set(targetId, [...decisions])
        const responsesById: Record<string, { decisions: Decision[] }> = {}
        const decisionsById = new Map<string, Decision[]>()
        for (const activeId of activeInterruptIds) {
          const pending = pendingInterruptDecisionsRef.current.get(activeId)
          if (!pending) return
          responsesById[activeId] = { decisions: pending }
          decisionsById.set(activeId, pending)
        }
        await stream.respondAll(responsesById)
        await refreshThreadLifecycleStream(stream)
        for (const [activeId, pending] of decisionsById) {
          pendingInterruptDecisionsRef.current.delete(activeId)
          rememberResolvedInterrupt(activeId, pending)
        }
        return
      }
      if (targetId) {
        const payload = allInterruptPayloadsById.get(targetId)
        const options = payload?.namespace
          ? { interruptId: targetId, namespace: payload.namespace }
          : { interruptId: targetId }
        await stream.respond(response, options)
        await refreshThreadLifecycleStream(stream)
        rememberResolvedInterrupt(targetId, decisions)
        return
      }
      await stream.respond(response)
      await refreshThreadLifecycleStream(stream)
      rememberResolvedInterrupt(null, decisions)
    },
    [
      allInterruptPayloadsById,
      firstInterruptId,
      interruptPayloads,
      rememberResolvedInterrupt,
      stream,
    ],
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
