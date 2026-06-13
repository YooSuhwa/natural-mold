'use client'

import { useCallback, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useChannelEffect, type AnyStream, type Event } from '@langchain/react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { memoryKeys } from '@/lib/hooks/use-memory'
import type { MemoryEventPayload, MemoryEventType } from '@/lib/types'

interface ProtocolMemoryEvent {
  readonly method?: string
  readonly event_id?: string
  readonly seq?: number
  readonly params?: {
    readonly data?: unknown
  }
}

interface UseLangGraphMemoryEffectsOptions {
  readonly stream: AnyStream
}

type ParsedMemoryEvent = {
  readonly eventName: MemoryEventType
  readonly payload: MemoryEventPayload
}

const MEMORY_CHANNELS = ['custom'] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function customName(event: ProtocolMemoryEvent): string | undefined {
  const method = textValue(event.method)
  if (method?.startsWith('custom:')) return method.slice(7)
  if (method !== 'custom' || !isRecord(event.params?.data)) return undefined
  return textValue(event.params.data.name) ?? textValue(event.params.data.channel)
}

function normalizeCustomName(name: string | undefined): string | undefined {
  if (!name) return undefined
  return name.startsWith('moldy.') ? name.slice('moldy.'.length) : name
}

function memoryEventName(name: string | undefined): MemoryEventType | null {
  switch (name) {
    case 'memory_proposed':
    case 'memory_saved':
    case 'memory_rejected':
    case 'memory_deleted':
      return name
    default:
      return null
  }
}

function payloadCandidate(data: unknown): unknown {
  if (isRecord(data) && isRecord(data.payload)) return data.payload
  return data
}

function isMemoryEventPayload(value: unknown): value is MemoryEventPayload {
  return (
    isRecord(value) &&
    (value.scope === 'user' || value.scope === 'agent') &&
    typeof value.content === 'string'
  )
}

export function protocolMemoryEvent(event: ProtocolMemoryEvent): ParsedMemoryEvent | null {
  const eventName = memoryEventName(normalizeCustomName(customName(event)))
  if (!eventName) return null

  const payload = payloadCandidate(event.params?.data)
  return isMemoryEventPayload(payload) ? { eventName, payload } : null
}

function memoryEventKey(event: ProtocolMemoryEvent, parsed: ParsedMemoryEvent): string {
  return (
    textValue(event.event_id) ??
    `${parsed.eventName}:${parsed.payload.id ?? parsed.payload.content}:${event.seq ?? 'no-seq'}`
  )
}

function showMemoryToast(eventName: MemoryEventType, tMemory: (key: string) => string): void {
  switch (eventName) {
    case 'memory_proposed':
      toast.info(tMemory('proposedToast'))
      return
    case 'memory_saved':
      toast.success(tMemory('savedToast'))
      return
    case 'memory_rejected':
      toast.warning(tMemory('rejectedToast'))
      return
    case 'memory_deleted':
      toast.success(tMemory('deletedToast'))
      return
  }
}

export function useLangGraphMemoryEffects({ stream }: UseLangGraphMemoryEffectsOptions): void {
  const queryClient = useQueryClient()
  const tMemory = useTranslations('chat.memory')
  const seenEventKeysRef = useRef(new Set<string>())

  const handleEvent = useCallback(
    (event: Event) => {
      const parsed = protocolMemoryEvent(event)
      if (!parsed) return

      const eventKey = memoryEventKey(event, parsed)
      if (seenEventKeysRef.current.has(eventKey)) return
      seenEventKeysRef.current.add(eventKey)

      queryClient.invalidateQueries({ queryKey: memoryKeys.all })
      showMemoryToast(parsed.eventName, tMemory)
    },
    [queryClient, tMemory],
  )

  useChannelEffect(stream, MEMORY_CHANNELS, {
    replay: false,
    bufferSize: 300,
    onEvent: handleEvent,
  })
}
