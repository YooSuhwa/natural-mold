import type { BaseMessage } from '@langchain/core/messages'
import { isAssistantMessage, isHumanMessage } from './stream-message-content'
import { baseMessageIdentity, messageContentFingerprint } from './stream-message-fingerprint'
import { isRecord } from './stream-message-utilities'

export function lastAssistantMessage(messages: readonly BaseMessage[]): BaseMessage | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (isAssistantMessage(message)) return message
  }
  return null
}

export function hasReadyAssistantMessage(messages: readonly BaseMessage[]): boolean {
  const lastAssistant = lastAssistantMessage(messages)
  return Boolean(lastAssistant && !isEmptyAssistantMessage(lastAssistant))
}

export function hasReadyAssistantAfterLastHumanMessage(messages: readonly BaseMessage[]): boolean {
  const lastHumanIndex = messages.findLastIndex(isHumanMessage)
  if (lastHumanIndex < 0) return hasReadyAssistantMessage(messages)
  return messages
    .slice(lastHumanIndex + 1)
    .some((message) => isAssistantMessage(message) && !isEmptyAssistantMessage(message))
}

export function messageListsSharePrefix(
  left: readonly BaseMessage[],
  right: readonly BaseMessage[],
  length: number,
): boolean {
  return messageListsSharedPrefixLength(left, right) >= length
}

export function messageListsSharedPrefixLength(
  left: readonly BaseMessage[],
  right: readonly BaseMessage[],
): number {
  const length = Math.min(left.length, right.length)
  let index = 0
  while (
    index < length &&
    messageContentFingerprint(left[index]) === messageContentFingerprint(right[index])
  ) {
    index += 1
  }
  return index
}

export function messageListIsOrderedSubsequence(
  candidate: readonly BaseMessage[],
  cached: readonly BaseMessage[],
): boolean {
  let cachedIndex = 0
  for (const candidateMessage of candidate) {
    let found = false
    while (cachedIndex < cached.length) {
      if (messagesShareStableIdentity(candidateMessage, cached[cachedIndex])) {
        found = true
        cachedIndex += 1
        break
      }
      cachedIndex += 1
    }
    if (!found) return false
  }
  return true
}

export function messagesShareStableIdentity(
  left: BaseMessage,
  right: BaseMessage | undefined,
): boolean {
  if (!right) return false
  const leftIdentity = baseMessageIdentity(left)
  const rightIdentity = baseMessageIdentity(right)
  if (leftIdentity && rightIdentity) return leftIdentity === rightIdentity
  return messageContentFingerprint(left) === messageContentFingerprint(right)
}

export function messageListIsDegraded(
  candidate: readonly BaseMessage[],
  cached: readonly BaseMessage[],
): boolean {
  if (candidate.length < cached.length) {
    if (messageListsSharePrefix(candidate, cached, candidate.length)) return true
    return messageListIsOrderedSubsequence(candidate, cached)
  }
  if (candidate.length !== cached.length || candidate.length === 0) return false

  const lastIndex = candidate.length - 1
  const candidateLast = candidate[lastIndex]
  const cachedLast = cached[lastIndex]
  return (
    messageListsSharePrefix(candidate, cached, lastIndex) &&
    isEmptyAssistantMessage(candidateLast) &&
    !isEmptyAssistantMessage(cachedLast)
  )
}

export function postRunHydrationIsReady(
  stateMessages: readonly BaseMessage[],
  visibleMessages: readonly BaseMessage[],
): boolean {
  if (!hasReadyAssistantAfterLastHumanMessage(stateMessages)) return false
  if (visibleMessages.length === 0) return true
  if (stateMessages.length < visibleMessages.length) return false
  if (messageListIsDegraded(stateMessages, visibleMessages)) return false
  return humanMessageCount(stateMessages) >= humanMessageCount(visibleMessages)
}

export function humanMessageCount(messages: readonly BaseMessage[]): number {
  return messages.filter(isHumanMessage).length
}

export function mergeCachedReadyAssistantMessages(
  candidate: readonly BaseMessage[],
  cached: readonly BaseMessage[],
): readonly BaseMessage[] {
  if (candidate.length === 0 || cached.length === 0) return candidate

  let changed = false
  const merged = candidate.map((message, index) => {
    const cachedMessage = cached[index]
    if (!cachedMessage) return message
    if (!canReuseCachedReadyAssistantMessage(message, cachedMessage)) return message
    changed = true
    return cachedMessage
  })
  return changed ? merged : candidate
}

export function canReuseCachedReadyAssistantMessage(
  candidate: BaseMessage,
  cached: BaseMessage,
): boolean {
  if (!isAssistantMessage(candidate) || !isAssistantMessage(cached)) return false
  return isEmptyAssistantMessage(candidate) && !isEmptyAssistantMessage(cached)
}

export function isEmptyAssistantMessage(message: BaseMessage | undefined): boolean {
  if (!message || typeof message._getType !== 'function' || message._getType() !== 'ai') {
    return false
  }
  if (assistantMessageHasToolCalls(message)) return false
  return isEmptyMessageContent(message.content)
}

export function suppressRunningEmptyAssistantPlaceholder(
  messages: readonly BaseMessage[],
  isRunning: boolean,
): readonly BaseMessage[] {
  if (!isRunning) return messages
  return isEmptyAssistantMessage(messages.at(-1)) ? messages.slice(0, -1) : messages
}

export function assistantMessageHasToolCalls(message: BaseMessage): boolean {
  const source = message as BaseMessage & {
    readonly additional_kwargs?: unknown
    readonly invalid_tool_calls?: unknown
    readonly tool_calls?: unknown
  }
  if (Array.isArray(source.tool_calls) && source.tool_calls.length > 0) return true
  if (Array.isArray(source.invalid_tool_calls) && source.invalid_tool_calls.length > 0) return true
  const additionalKwargs = isRecord(source.additional_kwargs) ? source.additional_kwargs : {}
  const additionalToolCalls = additionalKwargs.tool_calls
  return Array.isArray(additionalToolCalls) && additionalToolCalls.length > 0
}

export function isEmptyMessageContent(content: BaseMessage['content']): boolean {
  if (typeof content === 'string') return content.length === 0
  if (Array.isArray(content)) {
    return content.length === 0 || content.every(isEmptyTextContentPart)
  }
  return false
}

export function isEmptyTextContentPart(value: unknown): boolean {
  if (!isRecord(value)) return false
  if (value.type !== 'text') return false
  const text = value.text
  return text === undefined || text === ''
}
