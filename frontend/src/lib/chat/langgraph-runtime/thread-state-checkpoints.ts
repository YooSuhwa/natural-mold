'use client'

import type { MessageMetadataMap } from '@langchain/react'
import { apiFetch } from '@/lib/api/client'

export interface ServerCheckpointContext {
  readonly checkpointByMessageId: ReadonlyMap<string, string>
  readonly metadataByMessageId: MessageMetadataMap
  readonly messageIdsByIndex: readonly (readonly string[])[]
}

export interface ThreadStateResponse {
  readonly metadata?: unknown
  readonly values?: unknown
}

export async function loadServerThreadState(conversationId: string): Promise<ThreadStateResponse> {
  const encodedConversationId = encodeURIComponent(conversationId)
  return await apiFetch<ThreadStateResponse>(
    `/api/conversations/${encodedConversationId}/langgraph/threads/${encodedConversationId}/state`,
  )
}

export async function loadServerCheckpointContext(
  conversationId: string,
): Promise<ServerCheckpointContext> {
  const state = await loadServerThreadState(conversationId)
  return checkpointContextFromThreadState(state)
}

export function checkpointContextFromThreadState(
  state: ThreadStateResponse,
): ServerCheckpointContext {
  const metadata = isRecord(state.metadata) ? state.metadata : {}
  const checkpointByMessageId = stringMapFromRecord(metadata.checkpoint_by_message_id)
  const parentCheckpointByMessageId = stringMapFromRecord(metadata.parent_checkpoint_by_message_id)
  const metadataByMessageId: MessageMetadataMap = new Map(
    [...parentCheckpointByMessageId].map(([messageId, parentCheckpointId]) => [
      messageId,
      { parentCheckpointId },
    ]),
  )
  const values = isRecord(state.values) ? state.values : {}
  const messageIdsByIndex = orderedMessageIds(values.messages)
  return { checkpointByMessageId, metadataByMessageId, messageIdsByIndex }
}

function orderedMessageIds(value: unknown): readonly (readonly string[])[] {
  if (!Array.isArray(value)) return []
  const groups: string[][] = []
  for (const message of value) {
    if (!isRecord(message)) {
      groups.push([])
      continue
    }
    const id = message.id
    const ids = typeof id === 'string' && id.length > 0 ? [id] : []
    if (isToolMessageRecord(message) && groups.length > 0) {
      groups[groups.length - 1]?.push(...ids)
      continue
    }
    groups.push(ids)
  }
  return groups
}

function isToolMessageRecord(message: Record<string, unknown>): boolean {
  const role = message.role ?? message.type
  return role === 'tool' || role === 'tool_message' || role === 'ToolMessage'
}

function stringMapFromRecord(value: unknown): ReadonlyMap<string, string> {
  if (!isRecord(value)) return new Map()
  const entries = Object.entries(value).filter(
    (entry): entry is [string, string] => typeof entry[1] === 'string' && entry[1].length > 0,
  )
  return new Map(entries)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
