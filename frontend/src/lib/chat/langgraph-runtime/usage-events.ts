'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useChannel, type AnyStream } from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
import { useSetAtom } from 'jotai'
import { sessionTokenUsageAtom, type TokenUsage } from '@/lib/stores/chat-store'
import type { TokenUsageBreakdown } from '@/lib/types'
import {
  isRecord,
  protocolUsagePayload,
  textValue,
  usageFromMessage,
  type UsagePayload,
} from './usage-normalization'

type MessageWithUsage = BaseMessage & {
  readonly id?: string
  readonly additional_kwargs?: Record<string, unknown>
}

const EMPTY_USAGE_MAP: Record<string, TokenUsageBreakdown> = {}

interface UseLangGraphUsageEffectsOptions {
  readonly conversationId: string
  readonly stream: AnyStream
  readonly messages: readonly BaseMessage[]
  readonly stateMessages?: readonly BaseMessage[]
}

const USAGE_CHANNELS = ['messages', 'custom', 'custom:usage'] as const

function usageEventKey(event: unknown, payload: UsagePayload): string {
  const eventId = isRecord(event) ? textValue(event.event_id) : undefined
  const sequence = isRecord(event) && typeof event.seq === 'number' ? event.seq : 'no-seq'
  return eventId ?? `${payload.assistant_msg_id ?? payload.run_id ?? 'latest'}:${sequence}`
}

function usageMapKey(
  event: unknown,
  payload: UsagePayload,
  runMessageIds: ReadonlyMap<string, string>,
): string {
  const eventId = isRecord(event) ? textValue(event.event_id) : undefined
  const mappedMessageId = payload.run_id ? runMessageIds.get(payload.run_id) : undefined
  return payload.assistant_msg_id ?? mappedMessageId ?? payload.run_id ?? eventId ?? 'latest'
}

function updateUsageMap(
  current: Record<string, TokenUsageBreakdown>,
  key: string,
  usage: TokenUsageBreakdown,
  supersededKey?: string,
): Record<string, TokenUsageBreakdown> {
  const next = { ...current }
  const shouldDeleteSuperseded = Boolean(
    supersededKey && supersededKey !== key && supersededKey in next,
  )
  if (shouldDeleteSuperseded && supersededKey) {
    delete next[supersededKey]
  }
  // v3는 같은 메시지에 token-only(message-finish)와 timing/cost 포함(합성 usage)
  // 두 소스가 와서 서로 덮어쓴다. 비어 있는 timing/cost를 기존 값으로 backfill해
  // 어느 순서로 도착해도 유실되지 않게 한다.
  const merged = mergeUsageTiming(next[key], usage)
  if (sameUsage(next[key], merged) && !shouldDeleteSuperseded) {
    return current
  }
  return {
    ...next,
    [key]: merged,
  }
}

function migrateUsageMapKey(
  current: Record<string, TokenUsageBreakdown>,
  fromKey: string,
  toKey: string,
): Record<string, TokenUsageBreakdown> {
  if (fromKey === toKey || !(fromKey in current)) return current
  const next = { ...current }
  const usage = next[fromKey]
  delete next[fromKey]
  if (usage && !(toKey in next)) {
    next[toKey] = usage
  }
  return next
}

function sameUsage(
  left: TokenUsageBreakdown | undefined,
  right: TokenUsageBreakdown | undefined,
): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return (
    left.prompt_tokens === right.prompt_tokens &&
    left.completion_tokens === right.completion_tokens &&
    left.cache_creation_tokens === right.cache_creation_tokens &&
    left.cache_read_tokens === right.cache_read_tokens &&
    left.estimated_cost === right.estimated_cost &&
    left.ttft_ms === right.ttft_ms &&
    left.generation_ms === right.generation_ms &&
    left.tokens_per_second === right.tokens_per_second
  )
}

/**
 * v3는 같은 메시지에 usage 소스가 둘 — ① message-finish의 raw usage_metadata(token만),
 * ② 합성 `usage` 프로토콜 이벤트(token + cost + 스트리밍 timing). 둘이 같은 키로 매핑돼
 * 나중에 온 쪽이 덮어쓰므로 timing/cost가 한쪽에만 있으면 유실된다. 새 usage가 비운
 * timing/cost를 기존 값으로 backfill해 도착 순서와 무관하게 보존한다.
 */
function mergeUsageTiming(
  prev: TokenUsageBreakdown | undefined,
  next: TokenUsageBreakdown,
): TokenUsageBreakdown {
  if (!prev) return next
  const merged: TokenUsageBreakdown = { ...next }
  if (merged.ttft_ms === undefined && prev.ttft_ms !== undefined) merged.ttft_ms = prev.ttft_ms
  if (merged.generation_ms === undefined && prev.generation_ms !== undefined) {
    merged.generation_ms = prev.generation_ms
  }
  if (merged.tokens_per_second === undefined && prev.tokens_per_second !== undefined) {
    merged.tokens_per_second = prev.tokens_per_second
  }
  if (merged.estimated_cost === undefined && prev.estimated_cost !== undefined) {
    merged.estimated_cost = prev.estimated_cost
  }
  return merged
}

function sameTokenUsage(left: TokenUsage | null, right: TokenUsage): boolean {
  return (
    Boolean(left) &&
    left?.inputTokens === right.inputTokens &&
    left.outputTokens === right.outputTokens &&
    left.cost === right.cost
  )
}

function stableString(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === undefined) return ''
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function messageFingerprint(message: BaseMessage): string {
  const source = message as {
    readonly content?: unknown
    readonly additional_kwargs?: unknown
    readonly name?: unknown
    readonly tool_calls?: unknown
    readonly tool_call_id?: unknown
  }
  return stableString({
    id: messageId(message),
    kind: messageKind(message),
    content: source.content,
    additional_kwargs: source.additional_kwargs,
    name: source.name,
    tool_calls: source.tool_calls,
    tool_call_id: source.tool_call_id,
  })
}

function usageFingerprint(usage: TokenUsageBreakdown | undefined): string {
  if (!usage) return ''
  return stableString({
    prompt_tokens: usage.prompt_tokens,
    completion_tokens: usage.completion_tokens,
    cache_creation_tokens: usage.cache_creation_tokens,
    cache_read_tokens: usage.cache_read_tokens,
    estimated_cost: usage.estimated_cost,
    // timing이 token-only usage 뒤에 도착할 때 useStableUsageMap이 재메모화하도록 포함.
    ttft_ms: usage.ttft_ms,
    generation_ms: usage.generation_ms,
    tokens_per_second: usage.tokens_per_second,
  })
}

function usageMapFingerprint(usagesByMessageId: Record<string, TokenUsageBreakdown>): string {
  return Object.keys(usagesByMessageId)
    .sort()
    .map((key) => `${key}:${usageFingerprint(usagesByMessageId[key])}`)
    .join('|')
}

function useStableUsageMap(
  usagesByMessageId: Record<string, TokenUsageBreakdown>,
): Record<string, TokenUsageBreakdown> {
  const fingerprint = useMemo(() => usageMapFingerprint(usagesByMessageId), [usagesByMessageId])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => usagesByMessageId, [fingerprint])
}

function messagesWithUsageFingerprint(
  messages: readonly BaseMessage[],
  usagesByMessageId: Record<string, TokenUsageBreakdown>,
): string {
  return stableString({
    messages: messages.map(messageFingerprint),
    usages: usageMapFingerprint(usagesByMessageId),
  })
}

function messagePayloadAndMetadata(
  data: unknown,
): { payload: Record<string, unknown>; metadata: Record<string, unknown> } | null {
  if (Array.isArray(data) && data.length === 2 && isRecord(data[0])) {
    return {
      payload: data[0],
      metadata: isRecord(data[1]) ? data[1] : {},
    }
  }
  if (!isRecord(data)) return null
  return {
    payload: data,
    metadata: isRecord(data.metadata) ? data.metadata : {},
  }
}

function usageFromMessageEvent(
  event: unknown,
  runMessageIds: Map<string, string>,
): UsagePayload | null {
  if (!isRecord(event) || event.method !== 'messages') return null
  const params = isRecord(event.params) ? event.params : null
  const messageEvent = messagePayloadAndMetadata(params?.data)
  if (!messageEvent) return null
  const { payload: data, metadata } = messageEvent

  const runId = textValue(metadata.run_id)
  const messageId = textValue(data.id)
  if (data.event === 'message-start' && runId && messageId) {
    runMessageIds.set(runId, messageId)
    return null
  }
  if (data.event !== 'message-finish') return null

  const usage = protocolUsagePayload(data.usage)
  if (!usage) return null
  const assistantMsgId = runId ? runMessageIds.get(runId) : undefined
  return {
    ...usage,
    ...(runId ? { run_id: runId } : {}),
    ...(assistantMsgId ? { assistant_msg_id: assistantMsgId } : {}),
  }
}

function messageStartMapping(event: unknown): { runId: string; messageId: string } | null {
  if (!isRecord(event) || event.method !== 'messages') return null
  const params = isRecord(event.params) ? event.params : null
  const messageEvent = messagePayloadAndMetadata(params?.data)
  if (!messageEvent) return null
  const { payload: data, metadata } = messageEvent
  if (data.event !== 'message-start') return null
  const runId = textValue(metadata.run_id)
  const messageId = textValue(data.id)
  return runId && messageId ? { runId, messageId } : null
}

function messageId(message: BaseMessage): string | undefined {
  return textValue((message as { id?: unknown }).id)
}

function messageKind(message: BaseMessage): string | undefined {
  const maybeGetType = (message as { _getType?: unknown })._getType
  if (typeof maybeGetType === 'function') {
    const value = maybeGetType.call(message)
    return textValue(value)
  }
  return textValue((message as { type?: unknown }).type)
}

function isAssistantMessage(message: BaseMessage): boolean {
  const kind = messageKind(message)
  return kind === 'ai' || kind === 'assistant' || kind === 'AIMessage'
}

function isToolMessage(message: BaseMessage): boolean {
  const kind = messageKind(message)
  return kind === 'tool' || kind === 'ToolMessage'
}

function withUsage(message: BaseMessage, usage: TokenUsageBreakdown): MessageWithUsage {
  const source = message as MessageWithUsage
  const additionalKwargs = isRecord(source.additional_kwargs) ? source.additional_kwargs : {}
  const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  return Object.assign(Object.create(Object.getPrototypeOf(message)), message, {
    additional_kwargs: {
      ...additionalKwargs,
      metadata: {
        ...metadata,
        usage,
      },
    },
  }) as MessageWithUsage
}

function applyUsageToAssistantGroup(
  usageByIndex: Map<number, TokenUsageBreakdown>,
  messages: readonly BaseMessage[],
  index: number,
  usage: TokenUsageBreakdown,
): void {
  let start = index
  while (start > 0) {
    const previous = messages[start - 1]
    if (!previous || (!isAssistantMessage(previous) && !isToolMessage(previous))) break
    start -= 1
  }

  let end = index
  while (end < messages.length - 1) {
    const next = messages[end + 1]
    if (!next || (!isAssistantMessage(next) && !isToolMessage(next))) break
    end += 1
  }

  for (let groupIndex = start; groupIndex <= end; groupIndex += 1) {
    const message = messages[groupIndex]
    if (message && isAssistantMessage(message)) {
      usageByIndex.set(groupIndex, usage)
    }
  }
}

function attachUsageToMessages(
  messages: readonly BaseMessage[],
  usagesByMessageId: Record<string, TokenUsageBreakdown>,
): MessageWithUsage[] {
  const entries = Object.entries(usagesByMessageId)
  if (entries.length === 0) return messages as MessageWithUsage[]

  const messageIds = new Set(messages.map(messageId).filter((id): id is string => Boolean(id)))
  const unmatchedUsage = entries.find(([key]) => !messageIds.has(key))?.[1]
  const lastAssistantIndex = messages.findLastIndex(isAssistantMessage)
  const usageByIndex = new Map<number, TokenUsageBreakdown>()

  messages.forEach((message, index) => {
    const id = messageId(message)
    const exactUsage = id ? usagesByMessageId[id] : undefined
    if (exactUsage && isAssistantMessage(message)) {
      applyUsageToAssistantGroup(usageByIndex, messages, index, exactUsage)
    }
  })

  if (unmatchedUsage && lastAssistantIndex !== -1) {
    applyUsageToAssistantGroup(usageByIndex, messages, lastAssistantIndex, unmatchedUsage)
  }

  return messages.map((message, index) => {
    const usage = usageByIndex.get(index)
    return usage ? withUsage(message, usage) : message
  })
}

function usageFromMessages(messages: readonly BaseMessage[]): Record<string, TokenUsageBreakdown> {
  const usages: Record<string, TokenUsageBreakdown> = {}
  for (const message of messages) {
    const id = messageId(message)
    const usage = usageFromMessage(message)
    if (id && usage) {
      usages[id] = usage
    }
  }
  return usages
}

function sumUsage(usagesByMessageId: Record<string, TokenUsageBreakdown>): TokenUsage {
  return Object.values(usagesByMessageId).reduce<TokenUsage>(
    (total, usage) => ({
      inputTokens: total.inputTokens + usage.prompt_tokens,
      outputTokens: total.outputTokens + usage.completion_tokens,
      cost: total.cost + (usage.estimated_cost ?? 0),
    }),
    { inputTokens: 0, outputTokens: 0, cost: 0 },
  )
}

function updateScopedUsageState(
  current: {
    readonly conversationId: string
    readonly usagesByMessageId: Record<string, TokenUsageBreakdown>
  },
  conversationId: string,
  updater: (
    currentUsages: Record<string, TokenUsageBreakdown>,
  ) => Record<string, TokenUsageBreakdown>,
): {
  readonly conversationId: string
  readonly usagesByMessageId: Record<string, TokenUsageBreakdown>
} {
  const currentUsages = current.conversationId === conversationId ? current.usagesByMessageId : {}
  const nextUsages = updater(currentUsages)
  if (current.conversationId === conversationId && nextUsages === current.usagesByMessageId) {
    return current
  }
  return { conversationId, usagesByMessageId: nextUsages }
}

export function useLangGraphUsageEffects({
  conversationId,
  stream,
  messages,
  stateMessages = [],
}: UseLangGraphUsageEffectsOptions): MessageWithUsage[] {
  const setTokenUsage = useSetAtom(sessionTokenUsageAtom)
  const usageScope = useMemo(
    () => ({
      conversationId,
      runMessageIds: new Map<string, string>(),
      seenEventKeys: new Set<string>(),
    }),
    [conversationId],
  )
  const [eventUsageState, setEventUsageState] = useState<{
    readonly conversationId: string
    readonly usagesByMessageId: Record<string, TokenUsageBreakdown>
  }>({ conversationId, usagesByMessageId: {} })

  const handleEvent = useCallback(
    (event: unknown) => {
      const mapping = messageStartMapping(event)
      if (mapping) {
        usageScope.runMessageIds.set(mapping.runId, mapping.messageId)
        setEventUsageState((current) =>
          updateScopedUsageState(current, conversationId, (currentUsages) =>
            migrateUsageMapKey(currentUsages, mapping.runId, mapping.messageId),
          ),
        )
      }

      const payload =
        usageFromMessageEvent(event, usageScope.runMessageIds) ?? protocolUsagePayload(event)
      if (!payload) return

      const eventKey = usageEventKey(event, payload)
      if (usageScope.seenEventKeys.has(eventKey)) return
      usageScope.seenEventKeys.add(eventKey)

      const key = usageMapKey(event, payload, usageScope.runMessageIds)
      const { assistant_msg_id: _assistantMsgId, run_id: _runId, ...usage } = payload
      void _assistantMsgId
      void _runId
      setEventUsageState((current) =>
        updateScopedUsageState(current, conversationId, (currentUsages) =>
          updateUsageMap(currentUsages, key, usage, payload.run_id),
        ),
      )
    },
    [conversationId, usageScope],
  )

  const usageEvents = useChannel(stream, USAGE_CHANNELS, undefined, {
    replay: true,
    bufferSize: 300,
  })
  /* eslint-disable react-hooks/set-state-in-effect -- usage channel replay is an external stream snapshot; handleEvent dedupes by event id before updating local projection state. */
  useEffect(() => {
    for (const event of usageEvents) {
      handleEvent(event)
    }
  }, [handleEvent, usageEvents])
  /* eslint-enable react-hooks/set-state-in-effect */

  const messageUsagesByMessageId = useMemo(
    () => usageFromMessages([...messages, ...stateMessages]),
    [messages, stateMessages],
  )
  const stableMessageUsagesByMessageId = useStableUsageMap(messageUsagesByMessageId)
  const eventUsagesByMessageId = useMemo(
    () =>
      eventUsageState.conversationId === conversationId
        ? eventUsageState.usagesByMessageId
        : EMPTY_USAGE_MAP,
    [conversationId, eventUsageState],
  )
  const mergedUsagesByMessageId = useMemo(
    () => ({
      ...stableMessageUsagesByMessageId,
      ...eventUsagesByMessageId,
    }),
    [eventUsagesByMessageId, stableMessageUsagesByMessageId],
  )
  const usagesByMessageId = useStableUsageMap(mergedUsagesByMessageId)
  const totals = useMemo(() => sumUsage(usagesByMessageId), [usagesByMessageId])
  const lastTotalsRef = useRef<TokenUsage | null>(null)
  useEffect(() => {
    if (sameTokenUsage(lastTotalsRef.current, totals)) return
    lastTotalsRef.current = totals
    setTokenUsage(totals)
  }, [setTokenUsage, totals])

  const attachedFingerprint = useMemo(
    () => messagesWithUsageFingerprint(messages, usagesByMessageId),
    [messages, usagesByMessageId],
  )
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => attachUsageToMessages(messages, usagesByMessageId), [attachedFingerprint])
}
