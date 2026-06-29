'use client'

import { useCallback, useMemo, useRef, useState } from 'react'
import { useChannelEffect, type AnyStream, type Event } from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
import { type DataUIByMessageId, upsertMessageDataUI } from '@/lib/stores/chat-data-ui'
import type { UIDataEventPayload, UIDataItem } from '@/lib/types/ui-data'

/**
 * Generative UI ingestion (chat-generative-ui-dev-plan §5.2), modeled on
 * ``artifact-events.ts``. Consumes ``moldy.ui_data`` custom protocol events,
 * dedupes by tool_call_id, and attaches a ``uiData`` property to the assistant
 * message that made the tool call (else a last-assistant fallback). The
 * converter (path A) injects a data part from this property.
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
  conversationId: string
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
  // A single tool call yields exactly one ui_data payload, so dedup by
  // tool_call_id when present — the same logical event is re-delivered with
  // DIFFERENT event_id/run_id/seq (live broker, replay, and especially when a
  // later run re-synthesizes a prior turn's tool result from accumulated state),
  // which an event_id/run_id-based key would miss, double-rendering the card.
  const toolCallId = textValue(payload.tool_call_id)
  if (toolCallId) return `tc:${toolCallId}`
  return (
    textValue(event.event_id) ??
    `${payload.run_id ?? 'no-run'}:${payload.type}:${event.seq ?? 'no-seq'}`
  )
}

function attachKey(payload: UIDataEventPayload): string | undefined {
  return textValue(payload.message_id) ?? textValue(payload.run_id)
}

function itemFromPayload(payload: UIDataEventPayload): UIDataItem {
  return { type: payload.type, props: payload.props, tool_call_id: payload.tool_call_id ?? null }
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

/** Tool-call ids on an assistant message (top-level + additional_kwargs). */
function messageToolCallIds(message: BaseMessage): Set<string> {
  const ids = new Set<string>()
  const collect = (calls: unknown): void => {
    if (!Array.isArray(calls)) return
    for (const call of calls) {
      const id = textValue((call as { id?: unknown })?.id)
      if (id) ids.add(id)
    }
  }
  collect((message as { tool_calls?: unknown }).tool_calls)
  collect(
    (message as { additional_kwargs?: { tool_calls?: unknown } }).additional_kwargs?.tool_calls,
  )
  return ids
}

function withUIData(message: BaseMessage, uiData: UIDataItem[]): MessageWithUIData {
  return Object.assign(Object.create(Object.getPrototypeOf(message)), message, {
    uiData,
  }) as MessageWithUIData
}

/**
 * Attach each ui_data item to the assistant message that made its tool call
 * (``tool_call_id`` — which lives in the message content, so it survives
 * reload/state-hydration). This keeps per-turn items on the correct bubble; the
 * earlier ``run_id``-keyed last-assistant fallback collapsed every turn's items
 * onto the final assistant message (``run_id`` never equals the v3 LangChain
 * bubble id). Items without a matching tool call (no tool_call_id, or the AI
 * message hasn't arrived mid-stream yet) fall back to the last assistant
 * message — safe only because that case has a single in-flight item.
 */
export function attachDataUIToMessages(
  messages: readonly BaseMessage[],
  dataUIByMessageId: DataUIByMessageId,
): MessageWithUIData[] {
  const allItems = Object.values(dataUIByMessageId).flat()
  if (allItems.length === 0) return messages as MessageWithUIData[]

  const itemsByIndex = new Map<number, UIDataItem[]>()
  const matched = new Set<UIDataItem>()
  const pushItem = (index: number, item: UIDataItem): void => {
    const list = itemsByIndex.get(index)
    if (list) list.push(item)
    else itemsByIndex.set(index, [item])
  }

  messages.forEach((message, index) => {
    if (!isAssistantMessage(message)) return
    const toolIds = messageToolCallIds(message)
    if (toolIds.size === 0) return
    for (const item of allItems) {
      if (!matched.has(item) && item.tool_call_id && toolIds.has(item.tool_call_id)) {
        pushItem(index, item)
        matched.add(item)
      }
    }
  })

  const unmatched = allItems.filter((item) => !matched.has(item))
  const lastAssistantIndex = messages.findLastIndex(isAssistantMessage)
  if (unmatched.length > 0 && lastAssistantIndex >= 0) {
    for (const item of unmatched) pushItem(lastAssistantIndex, item)
  }

  if (itemsByIndex.size === 0) return messages as MessageWithUIData[]
  return messages.map((message, index) => {
    const items = itemsByIndex.get(index)
    return items && items.length > 0 ? withUIData(message, items) : message
  })
}

export function useLangGraphDataUIEffects({
  stream,
  conversationId,
  messages,
}: UseLangGraphDataUIEffectsOptions): MessageWithUIData[] {
  const seenEventKeysRef = useRef(new Set<string>())
  // Tie the accumulated items to a conversation id IN STATE so a conversation
  // switch (the hook instance can outlive it — the page isn't keyed) drops the
  // prior conversation's items instead of leaking them (via the fallback) into
  // the new one. The dedup ref only ever skips duplicates and its keys are
  // run-scoped, so it needs no reset.
  const [store, setStore] = useState<{ conversationId: string; items: DataUIByMessageId }>({
    conversationId,
    items: {},
  })
  const dataUIByMessageId = store.conversationId === conversationId ? store.items : {}

  const handleEvent = useCallback(
    (event: Event) => {
      const payload = protocolUIDataPayload(event)
      if (!payload) return
      const key = attachKey(payload)
      if (!key) return

      const eventKey = uiDataEventKey(event, payload)
      if (seenEventKeysRef.current.has(eventKey)) return
      seenEventKeysRef.current.add(eventKey)

      setStore((current) => {
        const items = current.conversationId === conversationId ? current.items : {}
        return { conversationId, items: upsertMessageDataUI(items, key, itemFromPayload(payload)) }
      })
    },
    // conversationId scopes the reset above; a switch re-subscribes the channel.
    [conversationId],
  )

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
