'use client'

import { useMemo } from 'react'
import { useChannel, type AnyStream } from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'

/**
 * Auto-compaction marker projection (dev-plan-context-compaction-marker.md).
 *
 * The backend emits a ``moldy.compaction`` custom event (running/done) when
 * deepagents summarizes older messages. The ``done`` event carries the offload
 * path but NOT a usable message id — its ``_summarization_event`` is committed
 * *after* the answer's ``message-start`` (verified ordering:
 * running → answer message-start → done). So we map each ``done`` to the LAST
 * ``message-start`` whose ``seq`` precedes it — that is the answer message for
 * the compacted turn. Replays preserve ``seq`` order, so the mapping is stable.
 */

export interface CompactionMarker {
  readonly offloadPath?: string
  readonly cutoffIndex?: number
}

type CompactionMessage = BaseMessage & {
  readonly id?: string
  readonly additional_kwargs?: Record<string, unknown>
}

interface ProtocolStreamEvent {
  readonly method?: string
  readonly seq?: number
  readonly event_id?: string
  readonly params?: {
    readonly data?: unknown
  }
}

const COMPACTION_CHANNELS = ['custom', 'messages'] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function customName(event: ProtocolStreamEvent): string | undefined {
  const method = textValue(event.method)
  if (method?.startsWith('custom:')) return method.slice(7)
  if (method !== 'custom' || !isRecord(event.params?.data)) return undefined
  return textValue(event.params.data.name) ?? textValue(event.params.data.channel)
}

function normalizeCustomName(name: string | undefined): string | undefined {
  if (!name) return undefined
  return name.startsWith('moldy.') ? name.slice('moldy.'.length) : name
}

function payloadCandidate(data: unknown): unknown {
  if (isRecord(data) && isRecord(data.payload)) return data.payload
  return data
}

function messagePayload(data: unknown): Record<string, unknown> | null {
  if (Array.isArray(data) && data.length === 2 && isRecord(data[0])) return data[0]
  return isRecord(data) ? data : null
}

export function compactionMarkerFromPayload(payload: unknown): CompactionMarker | null {
  if (!isRecord(payload) || payload.state !== 'done') return null
  const marker: { offloadPath?: string; cutoffIndex?: number } = {}
  const offloadPath = textValue(payload.offload_path) ?? textValue(payload.offloadPath)
  if (offloadPath) marker.offloadPath = offloadPath
  const cutoffIndex = numberValue(payload.cutoff_index ?? payload.cutoffIndex)
  if (cutoffIndex !== undefined) marker.cutoffIndex = cutoffIndex
  return marker
}

function compactionDoneFromEvent(event: ProtocolStreamEvent): CompactionMarker | null {
  if (normalizeCustomName(customName(event)) !== 'compaction') return null
  return compactionMarkerFromPayload(payloadCandidate(event.params?.data))
}

function messageStartIdFromEvent(event: ProtocolStreamEvent): string | null {
  if (event.method !== 'messages') return null
  const record = messagePayload(event.params?.data)
  if (!record || record.event !== 'message-start') return null
  return textValue(record.id) ?? null
}

interface SeqMessageStart {
  readonly seq: number
  readonly id: string
}

interface SeqCompactionDone {
  readonly seq: number
  readonly marker: CompactionMarker
}

export function computeCompactionByMessageId(
  events: readonly ProtocolStreamEvent[],
): Map<string, CompactionMarker> {
  const messageStarts: SeqMessageStart[] = []
  const dones: SeqCompactionDone[] = []
  events.forEach((event, index) => {
    const seq = numberValue(event.seq) ?? index
    const startId = messageStartIdFromEvent(event)
    if (startId) {
      messageStarts.push({ seq, id: startId })
      return
    }
    const marker = compactionDoneFromEvent(event)
    if (marker) dones.push({ seq, marker })
  })

  const byMessageId = new Map<string, CompactionMarker>()
  for (const done of dones) {
    let matched: SeqMessageStart | null = null
    for (const start of messageStarts) {
      if (start.seq <= done.seq && (matched === null || start.seq > matched.seq)) {
        matched = start
      }
    }
    if (matched) byMessageId.set(matched.id, done.marker)
  }
  return byMessageId
}

function messageId(message: BaseMessage): string | undefined {
  return textValue((message as { id?: unknown }).id)
}

function isAssistantMessage(message: BaseMessage): boolean {
  const getType = (message as { _getType?: unknown })._getType
  const kind = typeof getType === 'function' ? getType.call(message) : undefined
  return kind === 'ai' || kind === 'assistant'
}

function withCompaction(message: BaseMessage, marker: CompactionMarker): CompactionMessage {
  const source = message as CompactionMessage
  const additionalKwargs = isRecord(source.additional_kwargs) ? source.additional_kwargs : {}
  const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  return Object.assign(Object.create(Object.getPrototypeOf(message)), message, {
    additional_kwargs: {
      ...additionalKwargs,
      metadata: {
        ...metadata,
        compaction: marker,
      },
    },
  }) as CompactionMessage
}

export function compactionFromMessage(message: BaseMessage): CompactionMarker | null {
  const additionalKwargs = (message as { additional_kwargs?: unknown }).additional_kwargs
  const metadata =
    isRecord(additionalKwargs) && isRecord(additionalKwargs.metadata)
      ? additionalKwargs.metadata
      : null
  return isRecord(metadata?.compaction) ? (metadata.compaction as CompactionMarker) : null
}

export function attachCompactionToMessages(
  messages: readonly BaseMessage[],
  compactionByMessageId: ReadonlyMap<string, CompactionMarker>,
): BaseMessage[] {
  if (compactionByMessageId.size === 0) return messages as BaseMessage[]

  const messageIds = new Set(messages.map(messageId).filter((id): id is string => Boolean(id)))
  // Fallback (mirrors usage-events): a marker whose mapped message id is not in
  // the rendered list lands on the last assistant message of the turn.
  const unmatched = [...compactionByMessageId.entries()].find(([id]) => !messageIds.has(id))?.[1]
  const lastAssistantIndex = messages.findLastIndex(isAssistantMessage)

  return messages.map((message, index) => {
    const id = messageId(message)
    let marker = id ? compactionByMessageId.get(id) : undefined
    if (!marker && unmatched && index === lastAssistantIndex && isAssistantMessage(message)) {
      marker = unmatched
    }
    return marker ? withCompaction(message, marker) : message
  })
}

interface UseLangGraphCompactionEffectsOptions {
  readonly stream: AnyStream
  readonly messages: readonly BaseMessage[]
}

export function useLangGraphCompactionEffects({
  stream,
  messages,
}: UseLangGraphCompactionEffectsOptions): BaseMessage[] {
  const events = useChannel(stream, COMPACTION_CHANNELS, undefined, {
    replay: true,
    bufferSize: 300,
  })
  const compactionByMessageId = useMemo(
    () => computeCompactionByMessageId(events as readonly ProtocolStreamEvent[]),
    [events],
  )
  return useMemo(
    () => attachCompactionToMessages(messages, compactionByMessageId),
    [messages, compactionByMessageId],
  )
}
