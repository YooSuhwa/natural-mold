'use client'

import { useCallback, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useChannelEffect, type AnyStream, type Event } from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
import { useSetAtom } from 'jotai'
import { artifactKeys } from '@/lib/api/artifacts'
import { upsertArtifactList, upsertChatArtifactAtom } from '@/lib/stores/chat-artifacts'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import type { ArtifactSummary, FileEventPayload } from '@/lib/types'

interface ProtocolArtifactEvent {
  readonly method?: string
  readonly event_id?: string
  readonly seq?: number
  readonly run_id?: string
  readonly params?: {
    readonly data?: unknown
  }
}

type MessageWithArtifacts = BaseMessage & {
  readonly id?: string
  readonly artifacts?: ArtifactSummary[] | null
}

interface UseLangGraphArtifactEffectsOptions {
  stream: AnyStream
  conversationId: string
  messages: readonly BaseMessage[]
}

const ARTIFACT_CHANNELS = ['custom'] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function customName(event: ProtocolArtifactEvent): string | undefined {
  const method = textValue(event.method)
  if (method?.startsWith('custom:')) return method.slice(7)
  if (method !== 'custom' || !isRecord(event.params?.data)) return undefined
  return textValue(event.params.data.name) ?? textValue(event.params.data.channel)
}

function normalizeCustomName(name: string | undefined): string | undefined {
  if (!name) return undefined
  return name.startsWith('moldy.') ? name.slice('moldy.'.length) : name
}

function isArtifactCustomName(name: string | undefined): boolean {
  return name === 'artifact' || name === 'file' || name === 'file_event'
}

function payloadCandidate(data: unknown): unknown {
  if (isRecord(data) && isRecord(data.payload)) return data.payload
  return data
}

function isFileEventPayload(value: unknown): value is FileEventPayload {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    typeof value.conversation_id === 'string' &&
    typeof value.assistant_msg_id === 'string' &&
    typeof value.run_id === 'string' &&
    typeof value.path === 'string' &&
    typeof value.display_name === 'string' &&
    typeof value.op === 'string'
  )
}

export function protocolArtifactPayload(event: ProtocolArtifactEvent): FileEventPayload | null {
  const name = normalizeCustomName(customName(event))
  if (!isArtifactCustomName(name)) return null

  const payload = payloadCandidate(event.params?.data)
  return isFileEventPayload(payload) ? payload : null
}

function artifactEventKey(event: ProtocolArtifactEvent, payload: FileEventPayload): string {
  return (
    textValue(event.event_id) ??
    `${payload.id}:${payload.version_id}:${payload.op}:${event.seq ?? 'no-seq'}`
  )
}

function updateMessageArtifactMap(
  current: Record<string, ArtifactSummary[]>,
  payload: FileEventPayload,
): Record<string, ArtifactSummary[]> {
  const key = payload.assistant_msg_id || payload.run_id
  if (!key) return current

  const nextItems = upsertArtifactList(current[key] ?? [], payload)
  if (nextItems.length === 0) {
    const { [key]: _removed, ...rest } = current
    void _removed
    return rest
  }
  return {
    ...current,
    [key]: nextItems,
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

function withArtifacts(message: BaseMessage, artifacts: ArtifactSummary[]): MessageWithArtifacts {
  return Object.assign(Object.create(Object.getPrototypeOf(message)), message, {
    artifacts,
  }) as MessageWithArtifacts
}

function attachArtifactsToMessages(
  messages: readonly BaseMessage[],
  artifactsByMessageId: Record<string, ArtifactSummary[]>,
): MessageWithArtifacts[] {
  const entries = Object.entries(artifactsByMessageId)
  if (entries.length === 0) return messages as MessageWithArtifacts[]

  const messageIds = new Set(messages.map(messageId).filter((id): id is string => Boolean(id)))
  const unmatchedArtifacts = entries
    .filter(([key]) => !messageIds.has(key))
    .flatMap(([, artifacts]) => artifacts)
  const lastAssistantIndex = messages.findLastIndex(isAssistantMessage)

  return messages.map((message, index) => {
    const id = messageId(message)
    const exactArtifacts = id ? artifactsByMessageId[id] : undefined
    const fallbackArtifacts =
      !exactArtifacts && index === lastAssistantIndex ? unmatchedArtifacts : undefined
    const artifacts = exactArtifacts ?? fallbackArtifacts
    return artifacts && artifacts.length > 0 ? withArtifacts(message, artifacts) : message
  })
}

export function useLangGraphArtifactEffects({
  stream,
  conversationId,
  messages,
}: UseLangGraphArtifactEffectsOptions): MessageWithArtifacts[] {
  const queryClient = useQueryClient()
  const upsertArtifact = useSetAtom(upsertChatArtifactAtom)
  const setRightRail = useSetAtom(chatRightRailAtom)
  const seenEventKeysRef = useRef(new Set<string>())
  const [artifactsByMessageId, setArtifactsByMessageId] = useState<
    Record<string, ArtifactSummary[]>
  >({})

  const handleEvent = useCallback(
    (event: Event) => {
      const payload = protocolArtifactPayload(event)
      if (!payload || payload.conversation_id !== conversationId) return

      const eventKey = artifactEventKey(event, payload)
      if (seenEventKeysRef.current.has(eventKey)) return
      seenEventKeysRef.current.add(eventKey)

      upsertArtifact(payload)
      setArtifactsByMessageId((current) => updateMessageArtifactMap(current, payload))
      queryClient.invalidateQueries({ queryKey: artifactKeys.all })

      if (payload.op !== 'deleted') {
        setRightRail({
          mode: 'artifacts',
          artifacts: {
            conversationId: payload.conversation_id,
            selectedArtifactId: payload.id,
            view: 'preview',
          },
        })
      }
    },
    [conversationId, queryClient, setRightRail, upsertArtifact],
  )

  useChannelEffect(stream, ARTIFACT_CHANNELS, {
    replay: true,
    bufferSize: 300,
    onEvent: handleEvent,
  })

  return useMemo(
    () => attachArtifactsToMessages(messages, artifactsByMessageId),
    [messages, artifactsByMessageId],
  )
}
