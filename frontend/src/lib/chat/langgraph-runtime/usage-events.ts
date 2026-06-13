'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useChannelEffect, type AnyStream, type Event } from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
import { useSetAtom } from 'jotai'
import { sessionTokenUsageAtom, type TokenUsage } from '@/lib/stores/chat-store'
import type { TokenUsageBreakdown } from '@/lib/types'
import {
  isRecord,
  protocolUsagePayload,
  textValue,
  usageFromMessage,
  type ProtocolUsageEvent,
  type UsagePayload,
} from './usage-normalization'

type MessageWithUsage = BaseMessage & {
  readonly id?: string
  readonly additional_kwargs?: Record<string, unknown>
}

interface UseLangGraphUsageEffectsOptions {
  readonly stream: AnyStream
  readonly messages: readonly BaseMessage[]
}

const USAGE_CHANNELS = ['custom'] as const

function usageEventKey(event: ProtocolUsageEvent, payload: UsagePayload): string {
  return (
    textValue(event.event_id) ??
    `${payload.assistant_msg_id ?? payload.run_id ?? 'latest'}:${event.seq ?? 'no-seq'}`
  )
}

function usageMapKey(event: ProtocolUsageEvent, payload: UsagePayload): string {
  return payload.assistant_msg_id ?? payload.run_id ?? textValue(event.event_id) ?? 'latest'
}

function updateUsageMap(
  current: Record<string, TokenUsageBreakdown>,
  key: string,
  usage: TokenUsageBreakdown,
): Record<string, TokenUsageBreakdown> {
  return {
    ...current,
    [key]: usage,
  }
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

function attachUsageToMessages(
  messages: readonly BaseMessage[],
  usagesByMessageId: Record<string, TokenUsageBreakdown>,
): MessageWithUsage[] {
  const entries = Object.entries(usagesByMessageId)
  if (entries.length === 0) return messages as MessageWithUsage[]

  const messageIds = new Set(messages.map(messageId).filter((id): id is string => Boolean(id)))
  const unmatchedUsage = entries.find(([key]) => !messageIds.has(key))?.[1]
  const lastAssistantIndex = messages.findLastIndex(isAssistantMessage)

  return messages.map((message, index) => {
    const id = messageId(message)
    const exactUsage = id ? usagesByMessageId[id] : undefined
    const fallbackUsage = !exactUsage && index === lastAssistantIndex ? unmatchedUsage : undefined
    const usage = exactUsage ?? fallbackUsage
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
}: UseLangGraphUsageEffectsOptions): MessageWithUsage[] {
  const setTokenUsage = useSetAtom(sessionTokenUsageAtom)
  const seenEventKeysRef = useRef(new Set<string>())
  const [eventUsagesByMessageId, setEventUsagesByMessageId] = useState<
    Record<string, TokenUsageBreakdown>
  >({})

  const handleEvent = useCallback((event: Event) => {
    const payload = protocolUsagePayload(event)
    if (!payload) return

    const eventKey = usageEventKey(event, payload)
    if (seenEventKeysRef.current.has(eventKey)) return
    seenEventKeysRef.current.add(eventKey)

    const key = usageMapKey(event, payload)
    const { assistant_msg_id: _assistantMsgId, run_id: _runId, ...usage } = payload
    void _assistantMsgId
    void _runId
    setEventUsagesByMessageId((current) => updateUsageMap(current, key, usage))
  }, [])

  useChannelEffect(stream, USAGE_CHANNELS, {
    replay: true,
    bufferSize: 300,
    onEvent: handleEvent,
  })

  const messageUsagesByMessageId = useMemo(() => usageFromMessages(messages), [messages])
  const usagesByMessageId = useMemo(
    () => ({
      ...messageUsagesByMessageId,
      ...eventUsagesByMessageId,
    }),
    [eventUsagesByMessageId, messageUsagesByMessageId],
  )
  const totals = useMemo(() => sumUsage(usagesByMessageId), [usagesByMessageId])
  useEffect(() => {
    setTokenUsage(totals)
  }, [setTokenUsage, totals])

  return useMemo(
    () => attachUsageToMessages(messages, usagesByMessageId),
    [messages, usagesByMessageId],
  )
}
