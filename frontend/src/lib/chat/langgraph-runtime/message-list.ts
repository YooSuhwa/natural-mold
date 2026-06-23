'use client'

import { useMemo } from 'react'
import type { BaseMessage } from '@langchain/core/messages'

type MessageWithId = {
  readonly id?: unknown
  readonly role?: unknown
  readonly _getType?: unknown
}

type MessageTurnRole = 'user' | 'assistant' | 'tool'
const THREAD_MESSAGE_TURN_ID_SEPARATOR = '::moldy-turn-'

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
    readonly status?: unknown
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
    status: source.status,
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

export function sourceMessageIdFromThreadMessageId(messageId: unknown): string | null {
  if (typeof messageId !== 'string' || messageId.length === 0) return null
  const separatorIndex = messageId.lastIndexOf(THREAD_MESSAGE_TURN_ID_SEPARATOR)
  if (separatorIndex < 0) return messageId
  const suffix = messageId.slice(separatorIndex + THREAD_MESSAGE_TURN_ID_SEPARATOR.length)
  return /^\d+$/.test(suffix) ? messageId.slice(0, separatorIndex) : messageId
}

function messageId(message: MessageWithId): string | null {
  return typeof message.id === 'string' && message.id.length > 0 ? message.id : null
}

function messageTurnRole(message: MessageWithId): MessageTurnRole | null {
  if ('_getType' in message && typeof message._getType === 'function') {
    const type = message._getType()
    if (type === 'human') return 'user'
    if (type === 'ai') return 'assistant'
    if (type === 'tool') return 'tool'
  }
  if (!isRecord(message)) return null
  const role = message.role
  if (role === 'user' || role === 'assistant' || role === 'tool') return role
  return null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function hasArrayItems(value: unknown): boolean {
  return Array.isArray(value) && value.length > 0
}

function isEmptyTextContentPart(value: unknown): boolean {
  if (!isRecord(value)) return false
  const type = value.type
  if (type !== 'text') return false
  const text = value.text
  return text === undefined || text === ''
}

function isEmptyMessageContent(content: BaseMessage['content']): boolean {
  if (typeof content === 'string') return content.length === 0
  if (Array.isArray(content)) {
    return content.length === 0 || content.every(isEmptyTextContentPart)
  }
  return false
}

function isBlankAssistantPlaceholder(message: BaseMessage): boolean {
  if (typeof message._getType === 'function' && message._getType() !== 'ai') return false
  if (!isEmptyMessageContent(message.content)) return false

  const source = message as BaseMessage & {
    readonly additional_kwargs?: unknown
    readonly invalid_tool_calls?: unknown
    readonly tool_calls?: unknown
  }
  if (hasArrayItems(source.tool_calls) || hasArrayItems(source.invalid_tool_calls)) return false

  const additionalKwargs = isRecord(source.additional_kwargs) ? source.additional_kwargs : {}
  return !hasArrayItems(additionalKwargs.tool_calls)
}

export function suppressInitialEmptyAssistantPlaceholder(
  messages: readonly BaseMessage[],
  isRunning: boolean,
): readonly BaseMessage[] {
  if (!isRunning || messages.length === 0) return messages
  const lastMessage = messages.at(-1)
  return lastMessage && isBlankAssistantPlaceholder(lastMessage) ? messages.slice(0, -1) : messages
}

function threadMessageIdForTurn(sourceId: string, turnIndex: number): string {
  return `${sourceId}${THREAD_MESSAGE_TURN_ID_SEPARATOR}${turnIndex}`
}

function messageWithId<T extends MessageWithId>(
  message: T,
  id: string,
): T & { readonly id: string } {
  return { ...message, id }
}

function dedupeMessagesById<T extends MessageWithId>(
  messages: readonly T[],
  options: { readonly disambiguateCrossTurnIds?: boolean } = {},
): readonly T[] {
  const indexByScopedId = new Map<string, number>()
  const firstTurnBySourceId = new Map<string, number>()
  const deduped: T[] = []
  let changed = false
  let turnIndex = -1

  for (const message of messages) {
    if (messageTurnRole(message) === 'user') turnIndex += 1
    const id = messageId(message)
    if (id === null) {
      deduped.push(message)
      continue
    }

    const sourceId = sourceMessageIdFromThreadMessageId(id) ?? id
    const scopedId = `${turnIndex}:${sourceId}`
    const existingIndex = indexByScopedId.get(scopedId)
    if (existingIndex === undefined) {
      indexByScopedId.set(scopedId, deduped.length)
      const firstTurn = firstTurnBySourceId.get(sourceId)
      firstTurnBySourceId.set(sourceId, firstTurn ?? turnIndex)
      const nextId =
        options.disambiguateCrossTurnIds && firstTurn !== undefined && firstTurn !== turnIndex
          ? threadMessageIdForTurn(sourceId, turnIndex)
          : id
      const nextMessage = nextId === id ? message : messageWithId(message, nextId)
      if (nextMessage !== message) changed = true
      deduped.push(nextMessage)
      continue
    }

    const existingMessage = deduped[existingIndex]
    const existingId = existingMessage ? messageId(existingMessage) : null
    const nextMessage =
      options.disambiguateCrossTurnIds && existingId !== null && existingId !== id
        ? messageWithId(message, existingId)
        : message
    deduped[existingIndex] = nextMessage
    changed = true
  }

  return changed ? deduped : messages
}

export function dedupeThreadMessagesById<T extends MessageWithId>(
  messages: readonly T[],
): readonly T[] {
  return dedupeMessagesById(messages, { disambiguateCrossTurnIds: true })
}

export function useStableConvertedMessages<T extends MessageWithId>(
  messages: readonly T[],
  sourceMessages: readonly BaseMessage[],
  isRunning: boolean,
): readonly T[] {
  const deduped = useMemo(() => dedupeThreadMessagesById(messages), [messages])
  const fingerprint = stableString({
    status: isRunning ? 'running' : 'idle',
    source: sourceMessages.map(langChainMessageFingerprint),
  })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => deduped, [fingerprint])
}
