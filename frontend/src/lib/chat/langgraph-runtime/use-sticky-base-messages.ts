'use client'

import { useLayoutEffect, useMemo } from 'react'
import type { BaseMessage } from '@langchain/core/messages'
import { isHumanMessage } from './stream-message-content'
import {
  canReuseCachedReadyAssistantMessage,
  isEmptyMessageContent,
  mergeCachedReadyAssistantMessages,
  messageListIsDegraded,
  messageListsSharedPrefixLength,
} from './stream-message-comparison'
import { baseMessageIdentity } from './stream-message-fingerprint'
import { cacheStickyMessages, stickyMessagesByConversation } from './stream-message-foundation'

export function useStickyConversationMessages(
  conversationId: string,
  messages: readonly BaseMessage[],
  replaceMessages: boolean,
): readonly BaseMessage[] {
  // Perf: the merge pipeline below is worst-case O(n^2). Run it ONCE in the memo
  // and reuse the result in the layout effect instead of recomputing it. `cached`
  // is read only inside the effect (cache writes happen there), so the memo and
  // the effect observe the same cache snapshot within a single synchronous render.
  const { sticky, cacheable } = useMemo(() => {
    const cached = stickyMessagesByConversation.get(conversationId)
    const contentMergedMessages = cached
      ? mergeCachedNonEmptyMessageContent(messages, cached)
      : messages
    const mergedMessages =
      !replaceMessages && cached
        ? mergeCachedReadyAssistantMessages(
            mergeCachedPrefixForAppendOnlyStream(contentMergedMessages, cached),
            cached,
          )
        : contentMergedMessages
    if (!replaceMessages && cached && messageListIsDegraded(mergedMessages, cached)) {
      // Degraded candidate: keep showing the cached list and do not overwrite it.
      return { sticky: cached, cacheable: null as readonly BaseMessage[] | null }
    }
    return { sticky: mergedMessages, cacheable: mergedMessages }
  }, [conversationId, messages, replaceMessages])

  useLayoutEffect(() => {
    if (cacheable === null) return
    cacheStickyMessages(conversationId, cacheable)
  }, [cacheable, conversationId])

  return sticky
}

export function mergeCachedNonEmptyMessageContent(
  candidate: readonly BaseMessage[],
  cached: readonly BaseMessage[],
): readonly BaseMessage[] {
  if (candidate.length === 0 || cached.length === 0) return candidate

  let changed = false
  const usedCachedIndexes = new Set<number>()
  const merged = candidate.map((message, index) => {
    const cachedMessage = reusableCachedContentMessage(message, cached, index, usedCachedIndexes)
    if (!cachedMessage) return message
    changed = true
    return cachedMessage
  })
  return changed ? merged : candidate
}

export function reusableCachedContentMessage(
  candidate: BaseMessage,
  cached: readonly BaseMessage[],
  index: number,
  usedCachedIndexes: Set<number>,
): BaseMessage | null {
  const indexed = cached[index]
  if (
    indexed &&
    !usedCachedIndexes.has(index) &&
    canReuseCachedNonEmptyMessageContent(candidate, indexed)
  ) {
    usedCachedIndexes.add(index)
    return indexed
  }

  const identity = baseMessageIdentity(candidate)
  if (!identity) return null
  for (const [cachedIndex, cachedMessage] of cached.entries()) {
    if (usedCachedIndexes.has(cachedIndex)) continue
    if (baseMessageIdentity(cachedMessage) !== identity) continue
    if (!canReuseCachedNonEmptyMessageContent(candidate, cachedMessage)) continue
    usedCachedIndexes.add(cachedIndex)
    return cachedMessage
  }
  return null
}

export function canReuseCachedNonEmptyMessageContent(
  candidate: BaseMessage,
  cached: BaseMessage,
): boolean {
  if (isHumanMessage(candidate) && isHumanMessage(cached)) {
    return isEmptyMessageContent(candidate.content) && !isEmptyMessageContent(cached.content)
  }
  return canReuseCachedReadyAssistantMessage(candidate, cached)
}

export function mergeCachedPrefixForAppendOnlyStream(
  candidate: readonly BaseMessage[],
  cached: readonly BaseMessage[],
): readonly BaseMessage[] {
  if (candidate.length === 0 || cached.length === 0) return candidate
  const sharedPrefixLength = messageListsSharedPrefixLength(candidate, cached)
  if (sharedPrefixLength === 0) return candidate
  if (sharedPrefixLength === candidate.length || sharedPrefixLength === cached.length) {
    return candidate
  }
  const tail = candidate.slice(sharedPrefixLength)
  const firstTailMessage = tail[0]
  if (!firstTailMessage || !isHumanMessage(firstTailMessage)) return candidate
  const uncachedTail = tail.filter((message) => !cachedContainsStableIdentity(cached, message))
  return uncachedTail.length === 0 ? cached : [...cached, ...uncachedTail]
}

export function cachedContainsStableIdentity(
  cached: readonly BaseMessage[],
  candidate: BaseMessage,
): boolean {
  const identity = baseMessageIdentity(candidate)
  if (!identity) return false
  return cached.some((message) => baseMessageIdentity(message) === identity)
}
