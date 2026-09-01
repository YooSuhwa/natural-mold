'use client'

import { useLayoutEffect, useMemo } from 'react'
import {
  cacheStickyConvertedMessages,
  stickyConvertedMessagesByConversation,
} from './stream-message-foundation'
import type { ConvertedMessage } from './stream-message-types'
import { convertedMessageId } from './stream-message-fingerprint'
import {
  convertedMessageFingerprint,
  convertedMessageRole,
  isConvertedAssistantMessage,
  isEmptyConvertedMessage,
} from './stream-message-utilities'
import { isConvertedUserMessage } from './stream-message-fingerprint'
import { sourceMessageIdFromThreadMessageId } from './message-list'

export function useStickyConvertedMessages(
  conversationId: string,
  messages: readonly ConvertedMessage[],
  isRunning: boolean,
  replaceMessages: boolean,
): readonly ConvertedMessage[] {
  // Perf: as in useStickyConversationMessages, the convert merge is O(n^2). Run
  // it once in the memo and reuse the result in the effect rather than redoing it.
  const { sticky, cacheable } = useMemo(() => {
    const cached = stickyConvertedMessagesByConversation.get(conversationId)
    const mergedMessages = cached
      ? mergeCachedConvertedMessages(messages, cached, { reuseReadyAssistant: !isRunning })
      : messages
    const none = null as readonly ConvertedMessage[] | null
    if (!replaceMessages && messages.length === 0 && cached && cached.length > 0) {
      return { sticky: cached, cacheable: none }
    }
    if (!replaceMessages && cached && convertedMessageListIsDegraded(mergedMessages, cached)) {
      return { sticky: cached, cacheable: none }
    }
    return {
      sticky: mergedMessages,
      cacheable: mergedMessages.length > 0 ? mergedMessages : none,
    }
  }, [conversationId, isRunning, messages, replaceMessages])

  useLayoutEffect(() => {
    if (cacheable === null) return
    cacheStickyConvertedMessages(conversationId, cacheable)
  }, [cacheable, conversationId])

  return sticky
}

export function mergeCachedConvertedMessages(
  candidate: readonly ConvertedMessage[],
  cached: readonly ConvertedMessage[],
  options: { readonly reuseReadyAssistant: boolean },
): readonly ConvertedMessage[] {
  if (candidate.length === 0 || cached.length === 0) return candidate

  let changed = false
  const usedCachedIndexes = new Set<number>()
  const merged = candidate.map((message, index) => {
    const cachedMessage = reusableCachedConvertedMessage(
      message,
      cached,
      index,
      options,
      usedCachedIndexes,
    )
    if (!cachedMessage) return message
    changed = true
    return cachedMessage
  })
  return changed ? merged : candidate
}

export function reusableCachedConvertedMessage(
  candidate: ConvertedMessage,
  cached: readonly ConvertedMessage[],
  index: number,
  options: { readonly reuseReadyAssistant: boolean },
  usedCachedIndexes: Set<number>,
): ConvertedMessage | null {
  const indexed = cached[index]
  if (
    indexed &&
    !usedCachedIndexes.has(index) &&
    canReuseCachedConvertedMessage(candidate, indexed, options)
  ) {
    usedCachedIndexes.add(index)
    return indexed
  }

  const identity = convertedMessageIdentity(candidate)
  if (!identity) return null

  for (const [cachedIndex, cachedMessage] of cached.entries()) {
    if (usedCachedIndexes.has(cachedIndex)) continue
    if (convertedMessageIdentity(cachedMessage) !== identity) continue
    if (!canReuseCachedConvertedMessage(candidate, cachedMessage, options)) continue
    usedCachedIndexes.add(cachedIndex)
    return cachedMessage
  }

  return null
}

export function convertedMessageIdentity(message: ConvertedMessage): string | null {
  const id = convertedMessageId(message)
  if (!id) return null
  const role = convertedMessageRole(message)
  const sourceId = sourceMessageIdFromThreadMessageId(id) ?? id
  return role ? `${role}:${sourceId}` : sourceId
}

export function canReuseCachedConvertedMessage(
  candidate: ConvertedMessage,
  cached: ConvertedMessage,
  options: { readonly reuseReadyAssistant: boolean },
): boolean {
  if (
    isConvertedUserMessage(candidate) &&
    isConvertedUserMessage(cached) &&
    convertedMessageIdentity(candidate) === convertedMessageIdentity(cached)
  ) {
    return isEmptyConvertedMessage(candidate) && !isEmptyConvertedMessage(cached)
  }
  if (!options.reuseReadyAssistant) return false
  if (!isConvertedAssistantMessage(candidate) || !isConvertedAssistantMessage(cached)) return false
  return isEmptyConvertedMessage(candidate) && !isEmptyConvertedMessage(cached)
}

export function convertedMessageListIsDegraded(
  candidate: readonly ConvertedMessage[],
  cached: readonly ConvertedMessage[],
): boolean {
  if (candidate.length < cached.length) {
    return convertedMessagesSharePrefix(candidate, cached, candidate.length)
  }
  if (candidate.length !== cached.length || candidate.length === 0) return false

  const lastIndex = candidate.length - 1
  return (
    convertedMessagesSharePrefix(candidate, cached, lastIndex) &&
    isConvertedAssistantMessage(candidate[lastIndex]) &&
    isConvertedAssistantMessage(cached[lastIndex]) &&
    isEmptyConvertedMessage(candidate[lastIndex]) &&
    !isEmptyConvertedMessage(cached[lastIndex])
  )
}

export function convertedMessagesSharePrefix(
  left: readonly ConvertedMessage[],
  right: readonly ConvertedMessage[],
  length: number,
): boolean {
  if (left.length < length || right.length < length) return false
  return left
    .slice(0, length)
    .every(
      (message, index) =>
        convertedMessageFingerprint(message) === convertedMessageFingerprint(right[index]),
    )
}
