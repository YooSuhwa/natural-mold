'use client'

import { useEffect, useRef, useState } from 'react'
import { conversationsApi } from '@/lib/api/conversations'
import type { ChatRuntimeMode } from '@/lib/chat/runtime-mode'

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
  readonly isBootstrapping: boolean
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
  const onConversationIdRef = useRef(onConversationId)
  const conversationId = state.key === key ? state.conversationId : null
  const error = state.key === key ? state.error : null

  useEffect(() => {
    onConversationIdRef.current = onConversationId
  }, [onConversationId])

  useEffect(() => {
    if (key === null) {
      requestKeyRef.current = null
      queueMicrotask(() => {
        setState((current) => (current.key === null ? current : EMPTY_STATE))
      })
      return
    }
    if (conversationId !== null || requestKeyRef.current === key) return
    requestKeyRef.current = key
    const requestKey = key
    void conversationsApi
      .create(agentId)
      .then((conversation) => {
        if (requestKeyRef.current !== requestKey) return
        setState({ key: requestKey, conversationId: conversation.id, error: null })
        onConversationIdRef.current(conversation.id)
      })
      .catch((caught: unknown) => {
        if (requestKeyRef.current !== requestKey) return
        requestKeyRef.current = null
        setState({ key: requestKey, conversationId: null, error: caught })
      })
  }, [agentId, conversationId, key])

  if (error !== null) throw error
  return { conversationId, isBootstrapping: key !== null && conversationId === null }
}
