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
  appendInterruptToolCallMessages,
  appendResolvedInterruptToolCallMessages,
  resolvedInterruptToolCallsFromDecisions,
  standardPayloadsFromInterrupts,
  type ResolvedInterruptToolCall,
} from './hitl-interrupts'
import { useLangGraphMemoryEffects } from './memory-events'
import { createMoldyAgentTransport } from './moldy-agent-transport'
import { useCheckpointForkHandlers } from './use-checkpoint-fork-handlers'
import { useLangGraphUsageEffects } from './usage-events'
import { convertMoldyLangChainMessage } from './langchain-message-conversion'
import type { RunActivity } from './activity-model'
import { createHiTLDecisionCoordinator, type HiTLDecisionCoordinator } from '../standard-interrupt'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
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

interface LifecycleSubscription {
  unsubscribe(): Promise<void>
}

interface ThreadLifecycleSubscriber {
  subscribe(
    channel: 'lifecycle',
    options: { namespaces: string[][]; depth: number },
  ): Promise<LifecycleSubscription>
}

interface ThreadLifecycleStream {
  getThread(): ThreadLifecycleSubscriber | undefined
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

function stableString(value: unknown): string {
  if (value === undefined) return ''
  try {
    const seen = new WeakSet<object>()
    return JSON.stringify(value, (key, nestedValue: unknown) => {
      if (key === 'createdAt') return undefined
      if (typeof nestedValue === 'object' && nestedValue !== null) {
        if (seen.has(nestedValue)) return undefined
        seen.add(nestedValue)
      }
      return nestedValue
    })
  } catch {
    return String(value)
  }
}

function langChainMessageFingerprint(message: BaseMessage): string {
  const source = message as BaseMessage & {
    readonly additional_kwargs?: unknown
    readonly invalid_tool_calls?: unknown
    readonly response_metadata?: unknown
    readonly tool_call_id?: unknown
    readonly tool_calls?: unknown
    readonly usage_metadata?: unknown
  }
  return stableString({
    id: source.id,
    type: typeof source._getType === 'function' ? source._getType() : undefined,
    name: source.name,
    content: source.content,
    additional_kwargs: source.additional_kwargs,
    response_metadata: source.response_metadata,
    tool_calls: source.tool_calls,
    invalid_tool_calls: source.invalid_tool_calls,
    tool_call_id: source.tool_call_id,
    usage_metadata: source.usage_metadata,
  })
}

async function refreshThreadLifecycleStream(stream: ThreadLifecycleStream): Promise<void> {
  const thread = stream.getThread()
  if (!thread) return
  const subscription = await thread.subscribe('lifecycle', {
    namespaces: [[]],
    depth: 0,
  })
  await subscription.unsubscribe()
}

function useStableConvertedMessages<T extends object>(
  messages: readonly T[],
  sourceMessages: readonly BaseMessage[],
  isRunning: boolean,
): readonly T[] {
  const fingerprint = useMemo(
    () =>
      stableString({
        status: isRunning ? 'running' : 'idle',
        length: messages.length,
        source: sourceMessages.map(langChainMessageFingerprint),
      }),
    [isRunning, messages.length, sourceMessages],
  )

  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => messages, [fingerprint])
}

export function useMoldyLangGraphStream({
  agentId,
  conversationId,
  feedbackAdapter,
  attachmentAdapter,
}: UseMoldyLangGraphStreamOptions) {
  const setChatCancelInFlight = useSetAtom(chatCancelInFlightAtom)
  const activeRunIdRef = useRef<string | null>(null)
  const transport = useMemo(
    () => createMoldyAgentTransport(conversationId, agentId),
    [agentId, conversationId],
  )
  useEffect(() => {
    activeRunIdRef.current = null
  }, [conversationId])
  const onRunCreated = useCallback((run: { runId?: string | null }) => {
    activeRunIdRef.current = run.runId ?? null
  }, [])
  const onRunCompleted = useCallback(() => {
    activeRunIdRef.current = null
  }, [])
  const stream = useStream<MoldyGraphState>({
    transport,
    threadId: conversationId,
    onCreated: onRunCreated,
    onCompleted: onRunCompleted,
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
  const allInterruptPayloads = useMemo(
    () => standardPayloadsFromInterrupts(stream.interrupts),
    [stream.interrupts],
  )
  const resolvedInterruptIds = useMemo(
    () =>
      new Set(resolvedInterrupts.map((item) => String(item.toolCall.args.hitl_interrupt_id ?? ''))),
    [resolvedInterrupts],
  )
  const interruptPayloads = useMemo(
    () => allInterruptPayloads.filter((payload) => !resolvedInterruptIds.has(payload.interrupt_id)),
    [allInterruptPayloads, resolvedInterruptIds],
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
      appendResolvedInterruptToolCallMessages(
        appendInterruptToolCallMessages(stream.messages, interruptPayloads),
        resolvedInterrupts,
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
  const onCancel = useCallback(async () => {
    const runId = activeRunIdRef.current
    setChatCancelInFlight(true)
    try {
      if (runId) {
        await conversationRunsApi.cancel(conversationId, runId)
        activeRunIdRef.current = null
      }
    } finally {
      await stream.disconnect()
      setChatCancelInFlight(false)
    }
  }, [conversationId, setChatCancelInFlight, stream])

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
      if (targetId) {
        await stream.respond(response, { interruptId: targetId })
        await refreshThreadLifecycleStream(stream)
        rememberResolvedInterrupt(targetId, decisions)
        return
      }
      await stream.respond(response)
      await refreshThreadLifecycleStream(stream)
      rememberResolvedInterrupt(null, decisions)
    },
    [firstInterruptId, rememberResolvedInterrupt, stream],
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
