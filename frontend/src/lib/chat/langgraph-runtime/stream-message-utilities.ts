import type { BaseMessage } from '@langchain/core/messages'
import { sourceMessageIdFromThreadMessageId, stableString } from './message-list'
import type { ConvertedMessage, VisibleMessageWithId } from './stream-message-types'

export function convertedMessageFingerprint(message: ConvertedMessage | undefined): string {
  if (!isRecord(message)) return ''
  return stableString({
    id: message.id,
    role: message.role,
    content: message.content,
  })
}

export function isEmptyConvertedMessage(message: ConvertedMessage | undefined): boolean {
  if (!isRecord(message)) return false
  return isEmptyConvertedContent(message.content)
}

export function isEmptyConvertedContent(content: unknown): boolean {
  if (typeof content === 'string') return content.length === 0
  if (!Array.isArray(content)) return true
  if (content.length === 0) return true
  return content.every(isEmptyConvertedTextPart)
}

export function isEmptyConvertedTextPart(part: unknown): boolean {
  if (typeof part === 'string') return part.length === 0
  if (!isRecord(part)) return false
  if (part.type !== 'text') return false
  const text = part.text
  return text === undefined || text === ''
}

export function isConvertedAssistantMessage(message: ConvertedMessage | undefined): boolean {
  return isRecord(message) && message.role === 'assistant'
}

export function visibleMessagesWithIds(
  messages: readonly ConvertedMessage[],
  sourceMessages: readonly BaseMessage[] = [],
): readonly VisibleMessageWithId[] {
  const visibleMessages: VisibleMessageWithId[] = []
  for (const [index, message] of messages.entries()) {
    if (!('id' in message)) continue
    if (typeof message.id !== 'string' || message.id.length === 0) continue
    const role = convertedMessageRole(message) ?? langChainMessageRole(sourceMessages[index])
    const sourceId =
      sourceMessageIdFromThreadMessageId(message.id) ?? langChainMessageId(sourceMessages[index])
    visibleMessages.push({
      id: message.id,
      ...(role ? { role } : {}),
      ...(sourceId && sourceId !== message.id ? { sourceId } : {}),
    })
  }
  return visibleMessages
}

export function convertedMessageRole(message: ConvertedMessage): string | null {
  if (!isRecord(message)) return null
  return typeof message.role === 'string' && message.role.length > 0 ? message.role : null
}

export function langChainMessageRole(message: BaseMessage | undefined): string | null {
  if (!message || typeof message._getType !== 'function') return null
  const type = message._getType()
  if (type === 'human') return 'user'
  if (type === 'ai') return 'assistant'
  return type
}

export function langChainMessageId(message: BaseMessage | undefined): string | null {
  return typeof message?.id === 'string' && message.id.length > 0 ? message.id : null
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
