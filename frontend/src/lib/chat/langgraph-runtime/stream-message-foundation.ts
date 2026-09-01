import type { BaseMessage } from '@langchain/core/messages'
import { messagesFromThreadState } from './stream-thread-state-projection'
import type { PendingNewSubmitState } from './use-submit-checkpoint-controller'
import { isHumanMessage, messageContentEqualsText } from './stream-message-content'
import type { ConvertedMessage, SnapshotCloneableMessage } from './stream-message-types'
import { hasReadyAssistantMessage } from './stream-message-comparison'
import { isRecord } from './stream-message-utilities'

const STICKY_MESSAGE_CACHE_LIMIT = 50
export const stickyMessagesByConversation = new Map<string, readonly BaseMessage[]>()
export const stickyConvertedMessagesByConversation = new Map<string, readonly ConvertedMessage[]>()

export function primeStickyConversationMessagesFromThreadState(
  conversationId: string,
  state: unknown,
): boolean {
  const messages = messagesFromThreadState(state)
  if (!messages || messages.length === 0) return false
  if (!hasReadyAssistantMessage(messages)) return false
  cacheStickyMessages(conversationId, messages)
  return true
}

export function cacheStickyMessages(
  conversationId: string,
  messages: readonly BaseMessage[],
): void {
  if (messages.length === 0) return
  stickyMessagesByConversation.set(conversationId, snapshotBaseMessages(messages))
  if (stickyMessagesByConversation.size <= STICKY_MESSAGE_CACHE_LIMIT) return
  const oldestKey = stickyMessagesByConversation.keys().next().value
  if (typeof oldestKey === 'string') stickyMessagesByConversation.delete(oldestKey)
}

export function cacheStickyConvertedMessages(
  conversationId: string,
  messages: readonly ConvertedMessage[],
): void {
  if (messages.length === 0) return
  stickyConvertedMessagesByConversation.set(conversationId, messages)
  if (stickyConvertedMessagesByConversation.size <= STICKY_MESSAGE_CACHE_LIMIT) return
  const oldestKey = stickyConvertedMessagesByConversation.keys().next().value
  if (typeof oldestKey === 'string') stickyConvertedMessagesByConversation.delete(oldestKey)
}

export function clearStickyConversationMessages(conversationId: string): void {
  stickyMessagesByConversation.delete(conversationId)
  stickyConvertedMessagesByConversation.delete(conversationId)
}

export function snapshotBaseMessages(messages: readonly BaseMessage[]): readonly BaseMessage[] {
  return messages.map(cloneBaseMessageSnapshot)
}

export function cloneBaseMessageSnapshot(message: BaseMessage): BaseMessage {
  const source = message as SnapshotCloneableMessage
  const clone = Object.create(Object.getPrototypeOf(message)) as BaseMessage
  Object.assign(clone, message, {
    content: snapshotValue(message.content),
    additional_kwargs: snapshotValue(source.additional_kwargs),
    response_metadata: snapshotValue(source.response_metadata),
    status: snapshotValue(source.status),
    tool_calls: snapshotValue(source.tool_calls),
    invalid_tool_calls: snapshotValue(source.invalid_tool_calls),
    tool_call_id: snapshotValue(source.tool_call_id),
    usage_metadata: snapshotValue(source.usage_metadata),
  })
  return clone
}

export function snapshotValue<T>(value: T): T {
  if (value === null || value === undefined) return value
  if (typeof globalThis.structuredClone === 'function') {
    try {
      return globalThis.structuredClone(value)
    } catch {
      // Fall through for class instances or other non-cloneable values.
    }
  }
  if (Array.isArray(value)) {
    return value.map((item) => snapshotValue(item)) as T
  }
  if (isRecord(value)) {
    const cloned: Record<string, unknown> = {}
    for (const [key, nestedValue] of Object.entries(value)) {
      cloned[key] = snapshotValue(nestedValue)
    }
    return cloned as T
  }
  return value
}

export function appendPendingNewSubmitMessage(
  messages: readonly BaseMessage[],
  pendingNewSubmit: PendingNewSubmitState | null,
): readonly BaseMessage[] {
  if (!pendingNewSubmit) return messages
  const alreadyVisible = messages.some(
    (message) =>
      isHumanMessage(message) && messageContentEqualsText(message, pendingNewSubmit.content),
  )
  if (alreadyVisible) return messages
  // `baseMessageCount` was captured at submit time. If the raw list has SHRUNK
  // below it (messages dropped/replaced between capture and render), the captured
  // index points mid-list, so clamp the optimistic bubble to the tail. In the
  // normal (non-shrunk) case `baseMessageCount <= messages.length`, so this is
  // identical to inserting at the captured index.
  const insertionIndex =
    pendingNewSubmit.baseMessageCount <= messages.length
      ? pendingNewSubmit.baseMessageCount
      : messages.length
  return [
    ...messages.slice(0, insertionIndex),
    pendingNewSubmit.message,
    ...messages.slice(insertionIndex),
  ]
}
