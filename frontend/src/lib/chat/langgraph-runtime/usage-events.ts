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

interface UseLangGraphUsageEffectsOptions {
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
  if (sameUsage(next[key], usage) && !shouldDeleteSuperseded) {
    return current
  }
  return {
    ...next,
    [key]: usage,
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
    left.estimated_cost === right.estimated_cost
  )
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
    readonly name?: unknown
    readonly tool_calls?: unknown
    readonly tool_call_id?: unknown
  }
  return stableString({
    id: messageId(message),
    kind: messageKind(message),
    content: source.content,
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

export function useLangGraphUsageEffects({
  stream,
  messages,
  stateMessages = [],
}: UseLangGraphUsageEffectsOptions): MessageWithUsage[] {
  const setTokenUsage = useSetAtom(sessionTokenUsageAtom)
  const seenEventKeysRef = useRef(new Set<string>())
  const runMessageIdsRef = useRef(new Map<string, string>())
  const [eventUsagesByMessageId, setEventUsagesByMessageId] = useState<
    Record<string, TokenUsageBreakdown>
  >({})

  const handleEvent = useCallback((event: unknown) => {
    const mapping = messageStartMapping(event)
    if (mapping) {
      runMessageIdsRef.current.set(mapping.runId, mapping.messageId)
      setEventUsagesByMessageId((current) =>
        migrateUsageMapKey(current, mapping.runId, mapping.messageId),
      )
    }

    const payload =
      usageFromMessageEvent(event, runMessageIdsRef.current) ?? protocolUsagePayload(event)
    if (!payload) return

    const eventKey = usageEventKey(event, payload)
    if (seenEventKeysRef.current.has(eventKey)) return
    seenEventKeysRef.current.add(eventKey)

    const key = usageMapKey(event, payload, runMessageIdsRef.current)
    const { assistant_msg_id: _assistantMsgId, run_id: _runId, ...usage } = payload
    void _assistantMsgId
    void _runId
    setEventUsagesByMessageId((current) => updateUsageMap(current, key, usage, payload.run_id))
  }, [])

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
