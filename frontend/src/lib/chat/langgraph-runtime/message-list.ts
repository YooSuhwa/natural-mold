'use client'

import { useMemo } from 'react'
import type { BaseMessage } from '@langchain/core/messages'

type MessageWithId = {
  readonly id?: unknown
}

export function stableString(value: unknown): string {
  if (value === undefined) return ''
  try {
    const seen = new WeakSet<object>()
    return JSON.stringify(value, (key, nestedValue: unknown) => {
      if (key === 'createdAt') return undefined
      if (typeof nestedValue === 'object' && nestedValue !== null) {
        if (seen.has(nestedValue)) return undefined
        seen.add(nestedValue)
      }
      return nestedValue
    })
  } catch {
    return String(value)
  }
}

function langChainMessageFingerprint(message: BaseMessage): string {
  const source = message as BaseMessage & {
    readonly additional_kwargs?: unknown
    readonly invalid_tool_calls?: unknown
    readonly response_metadata?: unknown
    readonly tool_call_id?: unknown
    readonly tool_calls?: unknown
    readonly usage_metadata?: unknown
  }
  return stableString({
    id: source.id,
    type: typeof source._getType === 'function' ? source._getType() : undefined,
    name: source.name,
    content: source.content,
    additional_kwargs: source.additional_kwargs,
    response_metadata: source.response_metadata,
    tool_calls: source.tool_calls,
    invalid_tool_calls: source.invalid_tool_calls,
    tool_call_id: source.tool_call_id,
    usage_metadata: source.usage_metadata,
  })
}

export function dedupeLangChainMessagesById(
  messages: readonly BaseMessage[],
): readonly BaseMessage[] {
  return dedupeMessagesById(messages)
}

function messageId(message: MessageWithId): string | null {
  return typeof message.id === 'string' && message.id.length > 0 ? message.id : null
}

function dedupeMessagesById<T extends MessageWithId>(messages: readonly T[]): readonly T[] {
  const indexById = new Map<string, number>()
  const deduped: T[] = []
  let changed = false

  for (const message of messages) {
    const id = messageId(message)
    if (id === null) {
      deduped.push(message)
      continue
    }

    const existingIndex = indexById.get(id)
    if (existingIndex === undefined) {
      indexById.set(id, deduped.length)
      deduped.push(message)
      continue
    }

    deduped[existingIndex] = message
    changed = true
  }

  return changed ? deduped : messages
}

export function dedupeThreadMessagesById<T extends MessageWithId>(
  messages: readonly T[],
): readonly T[] {
  return dedupeMessagesById(messages)
}

export function useStableConvertedMessages<T extends MessageWithId>(
  messages: readonly T[],
  sourceMessages: readonly BaseMessage[],
  isRunning: boolean,
): readonly T[] {
  const deduped = useMemo(() => dedupeThreadMessagesById(messages), [messages])
  const fingerprint = useMemo(
    () =>
      stableString({
        status: isRunning ? 'running' : 'idle',
        length: deduped.length,
        source: sourceMessages.map(langChainMessageFingerprint),
        converted: deduped,
      }),
    [deduped, isRunning, sourceMessages],
  )

  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => deduped, [fingerprint])
}
