'use client'

import { useCallback, useMemo, useRef, useState } from 'react'
import { useChannelEffect, type AnyStream, type Event } from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
import { type DataUIByMessageId, upsertMessageDataUI } from '@/lib/stores/chat-data-ui'
import type { UIDataEventPayload, UIDataItem } from '@/lib/types/ui-data'

/**
 * Generative UI ingestion (chat-generative-ui-dev-plan §5.2) — a faithful clone
 * of ``artifact-events.ts``. Consumes ``moldy.ui_data`` custom protocol events,
 * dedupes by event key, and attaches a ``uiData`` property to messages (exact
 * assistant-message match, else last-assistant fallback). The converter
 * (path A) injects a data part from this property.
 */

interface ProtocolUIDataEvent {
  readonly method?: string
  readonly event_id?: string
  readonly seq?: number
  readonly run_id?: string
  readonly params?: {
    readonly data?: unknown
  }
}

type MessageWithUIData = BaseMessage & {
  readonly id?: string
  readonly uiData?: UIDataItem[] | null
}

interface UseLangGraphDataUIEffectsOptions {
  stream: AnyStream
  messages: readonly BaseMessage[]
}

const DATA_UI_CHANNELS = ['custom'] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function customName(event: ProtocolUIDataEvent): string | undefined {
  const method = textValue(event.method)
  if (method?.startsWith('custom:')) return method.slice(7)
  if (method !== 'custom' || !isRecord(event.params?.data)) return undefined
  return textValue(event.params.data.name) ?? textValue(event.params.data.channel)
}

function normalizeCustomName(name: string | undefined): string | undefined {
  if (!name) return undefined
  return name.startsWith('moldy.') ? name.slice('moldy.'.length) : name
}

function isUIDataCustomName(name: string | undefined): boolean {
  return name === 'ui_data'
}

function payloadCandidate(data: unknown): unknown {
  if (isRecord(data) && isRecord(data.payload)) return data.payload
  return data
}

function isUIDataPayload(value: unknown): value is UIDataEventPayload {
  return (
    isRecord(value) &&
    typeof value.type === 'string' &&
    value.type.length > 0 &&
    isRecord(value.props)
  )
}

export function protocolUIDataPayload(event: ProtocolUIDataEvent): UIDataEventPayload | null {
  const name = normalizeCustomName(customName(event))
  if (!isUIDataCustomName(name)) return null
  const payload = payloadCandidate(event.params?.data)
  return isUIDataPayload(payload) ? payload : null
}

function uiDataEventKey(event: ProtocolUIDataEvent, payload: UIDataEventPayload): string {
  return (
    textValue(event.event_id) ??
    `${payload.run_id ?? 'no-run'}:${payload.tool_call_id ?? 'no-call'}:${payload.type}:${event.seq ?? 'no-seq'}`
  )
}

function attachKey(payload: UIDataEventPayload): string | undefined {
  return textValue(payload.message_id) ?? textValue(payload.run_id)
}

function itemFromPayload(payload: UIDataEventPayload): UIDataItem {
  return { type: payload.type, props: payload.props, tool_call_id: payload.tool_call_id ?? null }
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

function withUIData(message: BaseMessage, uiData: UIDataItem[]): MessageWithUIData {
  return Object.assign(Object.create(Object.getPrototypeOf(message)), message, {
    uiData,
  }) as MessageWithUIData
}

export function attachDataUIToMessages(
  messages: readonly BaseMessage[],
  dataUIByMessageId: DataUIByMessageId,
): MessageWithUIData[] {
  const entries = Object.entries(dataUIByMessageId)
  if (entries.length === 0) return messages as MessageWithUIData[]

  const messageIds = new Set(messages.map(messageId).filter((id): id is string => Boolean(id)))
  const unmatched = entries.filter(([key]) => !messageIds.has(key)).flatMap(([, items]) => items)
  const lastAssistantIndex = messages.findLastIndex(isAssistantMessage)

  return messages.map((message, index) => {
    const id = messageId(message)
    const exact = id ? dataUIByMessageId[id] : undefined
    const fallback = !exact && index === lastAssistantIndex ? unmatched : undefined
    const items = exact ?? fallback
    return items && items.length > 0 ? withUIData(message, items) : message
  })
}

export function useLangGraphDataUIEffects({
  stream,
  messages,
}: UseLangGraphDataUIEffectsOptions): MessageWithUIData[] {
  const seenEventKeysRef = useRef(new Set<string>())
  const [dataUIByMessageId, setDataUIByMessageId] = useState<DataUIByMessageId>({})

  // The stream is already conversation-scoped, so no conversation filtering is
  // needed (unlike artifacts, ui_data payloads carry no conversation_id). Refs
  // and the state setter are stable, so the handler needs no deps.
  const handleEvent = useCallback((event: Event) => {
    const payload = protocolUIDataPayload(event)
    if (!payload) return
    const key = attachKey(payload)
    if (!key) return

    const eventKey = uiDataEventKey(event, payload)
    if (seenEventKeysRef.current.has(eventKey)) return
    seenEventKeysRef.current.add(eventKey)

    setDataUIByMessageId((current) => upsertMessageDataUI(current, key, itemFromPayload(payload)))
  }, [])

  useChannelEffect(stream, DATA_UI_CHANNELS, {
    replay: true,
    bufferSize: 300,
    onEvent: handleEvent,
  })

  return useMemo(
    () => attachDataUIToMessages(messages, dataUIByMessageId),
    [messages, dataUIByMessageId],
  )
}
