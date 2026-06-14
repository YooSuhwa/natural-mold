'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  useExternalMessageConverter,
  useExternalStoreRuntime,
  type AttachmentAdapter,
  type FeedbackAdapter,
} from '@assistant-ui/react'
import { useChannel, useStream, type Channel } from '@langchain/react'
import {
  AIMessage,
  HumanMessage,
  coerceMessageLikeToMessage,
  isBaseMessage,
  type BaseMessage,
  type BaseMessageLike,
} from '@langchain/core/messages'
import { useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import { reduceProtocolActivity } from './activity-protocol'
import { useLangGraphArtifactEffects } from './artifact-events'
import { MOLDY_BRANCH_SWITCHED_EVENT, isMoldyBranchSwitchedEvent } from './branch-switch-events'
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
import { loadServerThreadState, type ThreadStateResponse } from './thread-state-checkpoints'
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

interface ThreadRunNotice {
  readonly id: string
  readonly status: 'canceled' | 'canceling' | 'stale'
}

interface ThreadRunNoticeSnapshot {
  readonly conversationId: string
  readonly notice: ThreadRunNotice | null
}

interface ServerMessageMetadataSnapshot {
  readonly byId: ReadonlyMap<string, Record<string, unknown>>
  readonly byIndex: readonly (Record<string, unknown> | null)[]
  readonly idByIndex: readonly (string | null)[]
}

interface ServerMessageMetadataState {
  readonly conversationId: string
  readonly snapshot: ServerMessageMetadataSnapshot
}

interface ServerStateMessagesSnapshot {
  readonly conversationId: string
  readonly messages: readonly BaseMessage[]
}

interface ResolvedInterruptsSnapshot {
  readonly conversationId: string
  readonly items: readonly ResolvedInterruptToolCall[]
}

interface ThreadStateHydrationOptions {
  readonly replaceMessages?: boolean
}

const EMPTY_SERVER_MESSAGE_METADATA: ServerMessageMetadataSnapshot = {
  byId: new Map(),
  byIndex: [],
  idByIndex: [],
}
const EMPTY_RESOLVED_INTERRUPTS: readonly ResolvedInterruptToolCall[] = []

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
  getThread?: () => { readonly interrupts?: readonly LangGraphInterruptLike[] } | undefined
}): readonly LangGraphInterruptLike[] {
  return stream.getThread?.()?.interrupts ?? []
}

function terminalRunNoticeFromThreadState(state: unknown): ThreadRunNotice | null {
  if (!isRecord(state)) return null
  const metadata = isRecord(state.metadata) ? state.metadata : {}
  const run = isRecord(metadata.latest_run) ? metadata.latest_run : null
  const id = run?.id
  const status = run?.status
  if (typeof id !== 'string') return null
  if (status !== 'canceled' && status !== 'canceling' && status !== 'stale') return null
  return { id, status }
}

function messageMetadataFromThreadState(state: unknown): ServerMessageMetadataSnapshot {
  if (!isRecord(state)) return EMPTY_SERVER_MESSAGE_METADATA
  const values = isRecord(state.values) ? state.values : {}
  const messages = Array.isArray(values.messages) ? values.messages : []
  const metadataById = new Map<string, Record<string, unknown>>()
  const metadataByIndex: (Record<string, unknown> | null)[] = []
  const idByIndex: (string | null)[] = []
  for (const message of messages) {
    if (!isRecord(message)) {
      metadataByIndex.push(null)
      idByIndex.push(null)
      continue
    }
    const messageId = typeof message.id === 'string' && message.id.length > 0 ? message.id : null
    idByIndex.push(messageId)
    const additionalKwargs = isRecord(message.additional_kwargs) ? message.additional_kwargs : {}
    const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : null
    metadataByIndex.push(metadata)
    if (metadata && messageId) metadataById.set(messageId, metadata)
  }
  return { byId: metadataById, byIndex: metadataByIndex, idByIndex }
}

function messagesFromThreadState(state: unknown): readonly BaseMessage[] | null {
  if (!isRecord(state)) return null
  const values = isRecord(state.values) ? state.values : {}
  const messages = Array.isArray(values.messages) ? values.messages : null
  if (!messages) return null

  const converted: BaseMessage[] = []
  for (const message of messages) {
    if (!isCoercibleMessage(message)) continue
    converted.push(coerceMessageLikeToMessage(message))
  }
  return converted
}

function isCoercibleMessage(value: unknown): value is BaseMessageLike {
  if (isBaseMessage(value)) return true
  if (!isRecord(value)) return false
  const type = value.type
  const role = value.role
  return (typeof type === 'string' || typeof role === 'string') && 'content' in value
}

function mergeServerMessageMetadata(
  messages: readonly BaseMessage[],
  metadataSnapshot: ServerMessageMetadataSnapshot,
): BaseMessage[] {
  if (
    metadataSnapshot.byId.size === 0 &&
    metadataSnapshot.byIndex.length === 0 &&
    metadataSnapshot.idByIndex.length === 0
  ) {
    return [...messages]
  }
  return messages.map((message, index) => {
    const messageId = message.id
    const metadata =
      (messageId ? metadataSnapshot.byId.get(messageId) : undefined) ??
      metadataSnapshot.byIndex[index]
    const replacementId = replacementMessageId(message, metadataSnapshot.idByIndex[index])
    if (!metadata && !replacementId) return message
    return withAdditionalMessageMetadata(message, metadata ?? {}, replacementId)
  })
}

function replacementMessageId(
  message: BaseMessage,
  candidate: string | null | undefined,
): string | null {
  if (!candidate) return null
  const current = message.id
  if (typeof current !== 'string' || current.length === 0) return candidate
  if (current.startsWith('opt-') || current.startsWith('stream-')) return candidate
  return null
}

function withAdditionalMessageMetadata(
  message: BaseMessage,
  metadata: Record<string, unknown>,
  replacementId: string | null = null,
): BaseMessage {
  const additionalKwargs = isRecord(message.additional_kwargs) ? message.additional_kwargs : {}
  const existingMetadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  const clone = Object.create(Object.getPrototypeOf(message)) as BaseMessage
  Object.assign(clone, message, {
    ...(replacementId ? { id: replacementId } : {}),
    additional_kwargs: {
      ...additionalKwargs,
      metadata: {
        ...existingMetadata,
        ...metadata,
      },
    },
  })
  return clone
}

function appendTerminalRunNotice(
  messages: readonly BaseMessage[],
  notice: ThreadRunNotice | null,
  text: string,
): BaseMessage[] {
  if (!notice) return [...messages]
  const id = `moldy-${notice.status}-${notice.id}`
  if (messages.some((message) => message.id === id)) return [...messages]
  return [
    ...messages,
    new AIMessage({
      id,
      content: text,
      additional_kwargs: {
        metadata: {
          moldy_terminal_notice: notice.status,
        },
      },
    }),
  ]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function useMoldyLangGraphStream({
  agentId,
  conversationId,
  feedbackAdapter,
  attachmentAdapter,
}: UseMoldyLangGraphStreamOptions) {
  const setChatCancelInFlight = useSetAtom(chatCancelInFlightAtom)
  const tPage = useTranslations('chat.page')
  const tReconnect = useTranslations('chat.reconnect')
  const [threadRunNoticeState, setThreadRunNoticeState] = useState<ThreadRunNoticeSnapshot | null>(
    null,
  )
  const threadRunNotice =
    threadRunNoticeState?.conversationId === conversationId ? threadRunNoticeState.notice : null
  const setThreadRunNotice = useCallback(
    (notice: ThreadRunNotice | null) => {
      setThreadRunNoticeState({ conversationId, notice })
    },
    [conversationId],
  )
  const [serverMessageMetadataState, setServerMessageMetadataState] =
    useState<ServerMessageMetadataState | null>(null)
  const serverMessageMetadata =
    serverMessageMetadataState?.conversationId === conversationId
      ? serverMessageMetadataState.snapshot
      : EMPTY_SERVER_MESSAGE_METADATA
  const setServerMessageMetadata = useCallback(
    (snapshot: ServerMessageMetadataSnapshot) => {
      setServerMessageMetadataState({ conversationId, snapshot })
    },
    [conversationId],
  )
  const [serverStateMessages, setServerStateMessages] =
    useState<ServerStateMessagesSnapshot | null>(null)
  const handleThreadState = useCallback(
    (state: unknown, options?: ThreadStateHydrationOptions) => {
      setThreadRunNotice(terminalRunNoticeFromThreadState(state))
      setServerMessageMetadata(messageMetadataFromThreadState(state))
      if (options?.replaceMessages) {
        const messages = messagesFromThreadState(state)
        setServerStateMessages(messages ? { conversationId, messages } : null)
      }
    },
    [conversationId, setServerMessageMetadata, setThreadRunNotice],
  )
  const transport = useMemo(
    () => createMoldyAgentTransport(conversationId, agentId, { onState: handleThreadState }),
    [agentId, conversationId, handleThreadState],
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
  const [resolvedInterruptsState, setResolvedInterruptsState] =
    useState<ResolvedInterruptsSnapshot | null>(null)
  const resolvedInterrupts =
    resolvedInterruptsState?.conversationId === conversationId
      ? resolvedInterruptsState.items
      : EMPTY_RESOLVED_INTERRUPTS
  const setResolvedInterrupts = useCallback(
    (
      updater: (
        current: readonly ResolvedInterruptToolCall[],
      ) => readonly ResolvedInterruptToolCall[],
    ) => {
      setResolvedInterruptsState((current) => ({
        conversationId,
        items: updater(current?.conversationId === conversationId ? current.items : []),
      }))
    },
    [conversationId],
  )
  const wasLoadingRef = useRef(false)
  useEffect(() => {
    if (stream.isLoading) {
      wasLoadingRef.current = true
      return undefined
    }
    if (!wasLoadingRef.current) return undefined
    wasLoadingRef.current = false
    let cancelled = false
    void loadServerThreadState(conversationId)
      .then((state: ThreadStateResponse) => {
        if (!cancelled) handleThreadState(state)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [conversationId, handleThreadState, stream.isLoading])
  useEffect(() => {
    const onBranchSwitched = (event: Event) => {
      if (!isMoldyBranchSwitchedEvent(event)) return
      if (event.detail.conversationId !== conversationId) return
      void loadServerThreadState(conversationId)
        .then((state: ThreadStateResponse) => {
          handleThreadState(state, { replaceMessages: true })
        })
        .catch(() => undefined)
    }
    window.addEventListener(MOLDY_BRANCH_SWITCHED_EVENT, onBranchSwitched)
    return () => window.removeEventListener(MOLDY_BRANCH_SWITCHED_EVENT, onBranchSwitched)
  }, [conversationId, handleThreadState])
  const visibleStreamMessages =
    serverStateMessages?.conversationId === conversationId
      ? serverStateMessages.messages
      : stream.messages
  const streamMessagesWithServerMetadata = useMemo(
    () => mergeServerMessageMetadata(visibleStreamMessages, serverMessageMetadata),
    [serverMessageMetadata, visibleStreamMessages],
  )
  const allInterruptPayloads = standardPayloadsFromInterrupts([
    ...stream.interrupts,
    ...threadInterruptsFromStream(stream),
  ])
  const interruptPayloads = useMemo(
    () =>
      activeInterruptPayloads(
        allInterruptPayloads,
        streamMessagesWithServerMetadata,
        resolvedInterrupts,
      ),
    [allInterruptPayloads, resolvedInterrupts, streamMessagesWithServerMetadata],
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
          appendInterruptToolCallMessages(streamMessagesWithServerMetadata, interruptPayloads),
          resolvedInterrupts,
        ),
      ),
    [streamMessagesWithServerMetadata, interruptPayloads, resolvedInterrupts],
  )
  const messagesWithArtifacts = useLangGraphArtifactEffects({
    stream,
    conversationId,
    messages: messagesWithInterrupts,
  })
  const messagesWithUsage = useLangGraphUsageEffects({
    conversationId,
    stream,
    messages: messagesWithArtifacts,
    stateMessages: stream.values?.messages ?? [],
  })
  const terminalNoticeText =
    threadRunNotice?.status === 'stale' ? tReconnect('stale') : tPage('canceled')
  const messagesWithTerminalNotice = useMemo(
    () => appendTerminalRunNotice(messagesWithUsage, threadRunNotice, terminalNoticeText),
    [messagesWithUsage, terminalNoticeText, threadRunNotice],
  )
  useLangGraphMemoryEffects({ stream })
  const isRunning =
    stream.isLoading && interruptPayloads.length === 0 && threadRunNotice?.status !== 'stale'
  const convertedMessages = useExternalMessageConverter({
    callback: convertMoldyLangChainMessage,
    messages: messagesWithTerminalNotice,
    isRunning,
  })
  const messages = useStableConvertedMessages(
    convertedMessages,
    messagesWithTerminalNotice,
    isRunning,
  )
  const {
    onNew: submitNew,
    onEdit: submitEdit,
    onReload: submitReload,
  } = useCheckpointForkHandlers({
    conversationId,
    stream,
    visibleMessages: messages,
    langChainMessages: messagesWithTerminalNotice,
  })
  const coordinatorsRef = useRef(new Map<string, HiTLDecisionCoordinator>())
  const pendingInterruptDecisionsRef = useRef(new Map<string, Decision[]>())
  const pendingInterruptFlushRef = useRef(false)
  useEffect(() => {
    coordinatorsRef.current.clear()
    pendingInterruptDecisionsRef.current.clear()
    pendingInterruptFlushRef.current = false
  }, [conversationId])
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
      const activeRun = await conversationRunsApi.active(conversationId).catch(() => null)
      if (activeRun?.status === 'queued' || activeRun?.status === 'running') {
        await conversationRunsApi.cancel(conversationId, activeRun.id)
      }
      setThreadRunNotice({ id: `local-${conversationId}`, status: 'canceled' })
    } finally {
      setChatCancelInFlight(false)
    }
  }, [conversationId, setChatCancelInFlight, setThreadRunNotice, stream])

  const onNew = useCallback(
    async (...args: Parameters<typeof submitNew>) => {
      setThreadRunNotice(null)
      setServerStateMessages(null)
      await submitNew(...args)
    },
    [setThreadRunNotice, submitNew],
  )
  const onEdit = useCallback(
    async (...args: Parameters<typeof submitEdit>) => {
      setThreadRunNotice(null)
      setServerStateMessages(null)
      await submitEdit(...args)
    },
    [setThreadRunNotice, submitEdit],
  )
  const onReload = useCallback(
    async (...args: Parameters<typeof submitReload>) => {
      setThreadRunNotice(null)
      setServerStateMessages(null)
      await submitReload(...args)
    },
    [setThreadRunNotice, submitReload],
  )

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
    [allInterruptPayloadsById, setResolvedInterrupts],
  )

  const flushPendingInterruptDecisions = useCallback(
    async (activeInterruptIds: readonly string[]): Promise<boolean> => {
      if (pendingInterruptFlushRef.current || activeInterruptIds.length === 0) return false
      const decisionsById = new Map<string, Decision[]>()
      for (const activeId of activeInterruptIds) {
        const pending = pendingInterruptDecisionsRef.current.get(activeId)
        if (!pending) return false
        decisionsById.set(activeId, pending)
      }

      pendingInterruptFlushRef.current = true
      try {
        if (activeInterruptIds.length === 1) {
          const activeId = activeInterruptIds[0]
          const decisions = decisionsById.get(activeId)
          if (!activeId || !decisions) return false
          const payload = allInterruptPayloadsById.get(activeId)
          const options = payload?.namespace
            ? { interruptId: activeId, namespace: payload.namespace }
            : { interruptId: activeId }
          await stream.respond({ decisions }, options)
          await refreshThreadLifecycleStream(stream)
          pendingInterruptDecisionsRef.current.delete(activeId)
          rememberResolvedInterrupt(activeId, decisions)
          return true
        }

        const responsesById: Record<string, { decisions: Decision[] }> = {}
        for (const [activeId, decisions] of decisionsById) {
          responsesById[activeId] = { decisions }
        }
        await stream.respondAll(responsesById)
        await refreshThreadLifecycleStream(stream)
        for (const [activeId, decisions] of decisionsById) {
          pendingInterruptDecisionsRef.current.delete(activeId)
          rememberResolvedInterrupt(activeId, decisions)
        }
        return true
      } finally {
        pendingInterruptFlushRef.current = false
      }
    },
    [allInterruptPayloadsById, rememberResolvedInterrupt, stream],
  )

  useEffect(() => {
    const activeInterruptIds = interruptPayloads.map((payload) => payload.interrupt_id)
    const active = new Set(activeInterruptIds)
    for (const key of coordinatorsRef.current.keys()) {
      if (!active.has(key)) coordinatorsRef.current.delete(key)
    }
    for (const key of pendingInterruptDecisionsRef.current.keys()) {
      if (!active.has(key)) pendingInterruptDecisionsRef.current.delete(key)
    }
    void flushPendingInterruptDecisions(activeInterruptIds).catch(() => undefined)
  }, [flushPendingInterruptDecisions, interruptPayloads])

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
      setThreadRunNotice(null)
      setServerStateMessages(null)
      await stream.submit({ messages: [new HumanMessage(trimmed)] })
    },
    [setThreadRunNotice, stream],
  )
  const firstInterruptId = interruptPayloads[0]?.interrupt_id ?? null
  const onResumeDecisions = useCallback(
    async (decisions: Decision[], _displayText?: string, interruptId?: string | null) => {
      const targetId = interruptId ?? firstInterruptId
      const response = { decisions }
      const activeInterruptIds = interruptPayloads.map((payload) => payload.interrupt_id)
      if (targetId && activeInterruptIds.length > 1 && activeInterruptIds.includes(targetId)) {
        pendingInterruptDecisionsRef.current.set(targetId, [...decisions])
        await flushPendingInterruptDecisions(activeInterruptIds)
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
      flushPendingInterruptDecisions,
      interruptPayloads,
      rememberResolvedInterrupt,
      stream,
    ],
  )
  const onResumeDecisionsRef = useRef(onResumeDecisions)
  useEffect(() => {
    onResumeDecisionsRef.current = onResumeDecisions
  }, [onResumeDecisions])
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
          resume: (decisions, coordinatorDisplayText, coordinatorInterruptId) =>
            onResumeDecisionsRef.current(decisions, coordinatorDisplayText, coordinatorInterruptId),
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
