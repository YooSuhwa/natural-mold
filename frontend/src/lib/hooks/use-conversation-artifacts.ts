'use client'

import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSetAtom } from 'jotai'
import { artifactKeys, listConversationArtifacts } from '@/lib/api/artifacts'
import { setConversationArtifactsAtom } from '@/lib/stores/chat-artifacts'

export function useConversationArtifacts(conversationId: string | null | undefined) {
  const setConversationArtifacts = useSetAtom(setConversationArtifactsAtom)
  const query = useQuery({
    queryKey: artifactKeys.conversation(conversationId),
    queryFn: () => listConversationArtifacts(conversationId ?? ''),
    enabled: Boolean(conversationId),
    staleTime: 15_000,
  })

  useEffect(() => {
    if (!conversationId || !query.data) return
    setConversationArtifacts({ conversationId, items: query.data })
  }, [conversationId, query.data, setConversationArtifacts])

  return query
}
