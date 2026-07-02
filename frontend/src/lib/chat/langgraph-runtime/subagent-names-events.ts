'use client'

import { useCallback, useRef } from 'react'
import { useChannelEffect, type AnyStream, type Event } from '@langchain/react'
import { useSetAtom } from 'jotai'
import { mergeConversationSubagentNamesAtom } from '@/lib/stores/chat-subagent-names'

interface ProtocolSubagentNamesEvent {
  readonly method?: string
  readonly event_id?: string
  readonly seq?: number
  readonly params?: {
    readonly data?: unknown
  }
}

interface UseLangGraphSubagentNamesEffectsOptions {
  readonly stream: AnyStream
  readonly conversationId: string
}

const SUBAGENT_NAMES_CHANNELS = ['custom'] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function customName(event: ProtocolSubagentNamesEvent): string | undefined {
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

function parseSubagentNames(value: unknown): Record<string, string> | null {
  if (!isRecord(value) || !isRecord(value.names)) return null
  const names: Record<string, string> = {}
  for (const [runtimeName, displayName] of Object.entries(value.names)) {
    if (typeof displayName === 'string' && displayName.trim()) {
      names[runtimeName] = displayName
    }
  }
  return Object.keys(names).length > 0 ? names : null
}

export function protocolSubagentNames(
  event: ProtocolSubagentNamesEvent,
): Record<string, string> | null {
  if (normalizeCustomName(customName(event)) !== 'subagent_names') return null
  return parseSubagentNames(payloadCandidate(event.params?.data))
}

/**
 * Subscribe to the `moldy.subagent_names` custom side-channel and merge each
 * run's runtime_name -> display_name map into the conversation-scoped store.
 *
 * `replay: true` so the map is restored on reload/re-entry (unlike memory
 * toasts). Dedupe by the backend's stable `event_id` (`<run_id>:subagent_names`)
 * to avoid redundant atom writes; the seen set resets when the conversation
 * changes so a re-entry replay re-populates the store.
 */
export function useLangGraphSubagentNamesEffects({
  stream,
  conversationId,
}: UseLangGraphSubagentNamesEffectsOptions): void {
  const mergeNames = useSetAtom(mergeConversationSubagentNamesAtom)
  const seenRef = useRef<{ conversationId: string; keys: Set<string> }>({
    conversationId,
    keys: new Set(),
  })

  const handleEvent = useCallback(
    (event: Event) => {
      const names = protocolSubagentNames(event)
      if (!names) return
      if (seenRef.current.conversationId !== conversationId) {
        seenRef.current = { conversationId, keys: new Set() }
      }
      const key = textValue(event.event_id) ?? `subagent_names:${event.seq ?? 'no-seq'}`
      if (seenRef.current.keys.has(key)) return
      seenRef.current.keys.add(key)
      mergeNames({ conversationId, names })
    },
    [conversationId, mergeNames],
  )

  useChannelEffect(stream, SUBAGENT_NAMES_CHANNELS, {
    replay: true,
    bufferSize: 300,
    onEvent: handleEvent,
  })
}
