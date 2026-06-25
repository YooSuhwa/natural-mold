'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { conversationsApi } from '@/lib/api/conversations'
import type { ChatRuntimeMode } from '@/lib/chat/runtime-mode'
import { reportClientWarning } from '@/lib/logging/client-logger'

interface DraftConversationState {
  readonly key: string | null
  readonly conversationId: string | null
  readonly error: unknown
}

interface UseLangGraphDraftConversationOptions {
  readonly agentId: string
  readonly isDraftConversation: boolean
  readonly runtimeMode: ChatRuntimeMode
  readonly onConversationId: (conversationId: string) => void
}

interface UseLangGraphDraftConversationResult {
  readonly conversationId: string | null
  readonly error: unknown
  readonly isBootstrapping: boolean
  readonly retainDraftConversation: () => string | null
  readonly commitDraftConversation: () => string | null
}

const EMPTY_STATE: DraftConversationState = {
  key: null,
  conversationId: null,
  error: null,
}

function draftKey(
  agentId: string,
  isDraftConversation: boolean,
  runtimeMode: ChatRuntimeMode,
): string | null {
  return isDraftConversation && runtimeMode === 'langgraph_v3' ? agentId : null
}

export function useLangGraphDraftConversation({
  agentId,
  isDraftConversation,
  runtimeMode,
  onConversationId,
}: UseLangGraphDraftConversationOptions): UseLangGraphDraftConversationResult {
  const key = draftKey(agentId, isDraftConversation, runtimeMode)
  const [state, setState] = useState<DraftConversationState>(EMPTY_STATE)
  const requestKeyRef = useRef<string | null>(null)
  const requestTokenRef = useRef<symbol | null>(null)
  const activeKeyRef = useRef<string | null>(null)
  const committedConversationIdsRef = useRef(new Set<string>())
  const stateRef = useRef<DraftConversationState>(EMPTY_STATE)
  const onConversationIdRef = useRef(onConversationId)
  const conversationId = state.key === key ? state.conversationId : null
  const error = state.key === key ? state.error : null

  useEffect(() => {
    stateRef.current = state
  }, [state])

  useEffect(() => {
    onConversationIdRef.current = onConversationId
  }, [onConversationId])

  useEffect(() => {
    const committedConversationIds = committedConversationIdsRef.current
    activeKeyRef.current = key
    if (key === null) {
      requestKeyRef.current = null
      requestTokenRef.current = null
      queueMicrotask(() => {
        setState((current) => (current.key === null ? current : EMPTY_STATE))
      })
      return
    }
    return () => {
      const cleanupKey = key
      activeKeyRef.current = null
      queueMicrotask(() => {
        if (activeKeyRef.current === cleanupKey) return
        if (requestKeyRef.current === cleanupKey) {
          requestKeyRef.current = null
          requestTokenRef.current = null
        }
        setState((current) => (current.key === cleanupKey ? EMPTY_STATE : current))
        const draftConversationId =
          stateRef.current.key === cleanupKey ? stateRef.current.conversationId : null
        if (draftConversationId === null || committedConversationIds.has(draftConversationId)) {
          return
        }
        void conversationsApi.delete(draftConversationId).catch((caught: unknown) => {
          reportClientWarning('useLangGraphDraftConversation', 'delete draft failed', caught)
        })
      })
    }
  }, [key])

  useEffect(() => {
    if (key === null) return
    if (conversationId !== null || requestKeyRef.current === key) return
    requestKeyRef.current = key
    const requestKey = key
    const requestToken = Symbol(requestKey)
    requestTokenRef.current = requestToken
    void conversationsApi
      .createDraft(agentId)
      .then((conversation) => {
        if (
          activeKeyRef.current !== requestKey ||
          requestKeyRef.current !== requestKey ||
          requestTokenRef.current !== requestToken
        ) {
          if (!committedConversationIdsRef.current.has(conversation.id)) {
            void conversationsApi.delete(conversation.id).catch((caught: unknown) => {
              reportClientWarning('useLangGraphDraftConversation', 'delete draft failed', caught)
            })
          }
          return
        }
        setState({ key: requestKey, conversationId: conversation.id, error: null })
        onConversationIdRef.current(conversation.id)
      })
      .catch((caught: unknown) => {
        if (
          activeKeyRef.current !== requestKey ||
          requestKeyRef.current !== requestKey ||
          requestTokenRef.current !== requestToken
        ) {
          return
        }
        setState({ key: requestKey, conversationId: null, error: caught })
      })
  }, [agentId, conversationId, key])

  const retainDraftConversation = useCallback((): string | null => {
    const current = stateRef.current
    const currentConversationId = current.key === key ? current.conversationId : null
    if (currentConversationId) committedConversationIdsRef.current.add(currentConversationId)
    return currentConversationId
  }, [key])

  // `commitDraftConversation` is an intentional alias of `retainDraftConversation`
  // with identical effect. The two names exist purely for caller readability: a
  // caller "retains" the draft before sending a message (keep it alive), and
  // "commits" it once the run is accepted. Keep both exports — page.tsx (owned by
  // another module) calls them by name.
  const commitDraftConversation = retainDraftConversation

  return {
    conversationId,
    error,
    isBootstrapping: key !== null && conversationId === null && error === null,
    retainDraftConversation,
    commitDraftConversation,
  }
}
