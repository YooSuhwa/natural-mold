'use client'

import { useLayoutEffect } from 'react'
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

  // A failed refetch can retain old rows in the query cache. Keep the rail and
  // its download actions in sync with the current viewer's lookup before paint.
  const data = query.isError ? undefined : query.data
  useLayoutEffect(() => {
    if (conversationId && (query.isError || data)) {
      setConversationArtifacts({ conversationId, items: data ?? [] })
    }
  }, [conversationId, data, query.isError, setConversationArtifacts])

  return query.isError ? { ...query, data: undefined } : query
}
