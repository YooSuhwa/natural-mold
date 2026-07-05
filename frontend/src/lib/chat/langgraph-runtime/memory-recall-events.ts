'use client'

import { useCallback, useRef } from 'react'
import { useChannelEffect, type AnyStream, type Event } from '@langchain/react'
import { useSetAtom } from 'jotai'
import {
  setConversationMemoryRecallAtom,
  type RecalledMemoryBrief,
} from '@/lib/stores/chat-memory-recall'

interface ProtocolMemoryRecallEvent {
  readonly method?: string
  readonly event_id?: string
  readonly seq?: number
  readonly params?: {
    readonly data?: unknown
  }
}

interface UseLangGraphMemoryRecallEffectsOptions {
  readonly stream: AnyStream
  readonly conversationId: string
}

const MEMORY_RECALL_CHANNELS = ['custom'] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function customName(event: ProtocolMemoryRecallEvent): string | undefined {
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

function parseRecalledMemories(value: unknown): RecalledMemoryBrief[] | null {
  if (!isRecord(value) || !Array.isArray(value.memories)) return null
  const briefs: RecalledMemoryBrief[] = []
  for (const entry of value.memories) {
    if (!isRecord(entry)) continue
    const scope = entry.scope === 'agent' ? 'agent' : entry.scope === 'user' ? 'user' : null
    const content = textValue(entry.content)
    if (!scope || !content) continue
    briefs.push({
      id: textValue(entry.id),
      scope,
      content,
    })
  }
  return briefs.length > 0 ? briefs : null
}

export function protocolMemoryRecall(
  event: ProtocolMemoryRecallEvent,
): RecalledMemoryBrief[] | null {
  if (normalizeCustomName(customName(event)) !== 'memory_recalled') return null
  return parseRecalledMemories(payloadCandidate(event.params?.data))
}

/**
 * Subscribe to the `moldy.memory_recalled` custom side-channel and store each
 * run's recall briefs into the conversation-scoped atom (latest run replaces).
 *
 * `replay: true`라 리로드/재진입 시에도 칩이 복원된다(subagent_names 계약과
 * 동일). Dedupe by the backend's stable `event_id`
 * (`<run_id>:memory_recalled`); the seen set resets when the conversation
 * changes so a re-entry replay re-populates the store.
 */
export function useLangGraphMemoryRecallEffects({
  stream,
  conversationId,
}: UseLangGraphMemoryRecallEffectsOptions): void {
  const setRecall = useSetAtom(setConversationMemoryRecallAtom)
  const seenRef = useRef<{ conversationId: string; keys: Set<string> }>({
    conversationId,
    keys: new Set(),
  })

  const handleEvent = useCallback(
    (event: Event) => {
      const memories = protocolMemoryRecall(event)
      if (!memories) return
      if (seenRef.current.conversationId !== conversationId) {
        seenRef.current = { conversationId, keys: new Set() }
      }
      const key = textValue(event.event_id) ?? `memory_recalled:${event.seq ?? 'no-seq'}`
      if (seenRef.current.keys.has(key)) return
      seenRef.current.keys.add(key)
      setRecall({ conversationId, memories })
    },
    [conversationId, setRecall],
  )

  useChannelEffect(stream, MEMORY_RECALL_CHANNELS, {
    replay: true,
    bufferSize: 300,
    onEvent: handleEvent,
  })
}
