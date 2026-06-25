'use client'

import type { InfiniteData } from '@tanstack/react-query'
import { useQueryClient } from '@tanstack/react-query'
import { useConversationDetail, conversationKeys } from '@/lib/hooks/use-conversations'
import type {
  Conversation,
  ConversationListEnvelope,
  ConversationWithAgentListEnvelope,
} from '@/lib/types'

function findConversationTitle(
  conversations: readonly Conversation[] | undefined,
  conversationId: string,
): string | null {
  const found = conversations?.find((conversation) => conversation.id === conversationId)
  return found?.title ?? null
}

function findTitleInPages<T extends { items: readonly Conversation[] }>(
  pages: readonly [readonly unknown[], InfiniteData<T> | undefined][],
  conversationId: string,
): string | null {
  for (const [, data] of pages) {
    const title = findConversationTitle(
      data?.pages.flatMap((page) => page.items),
      conversationId,
    )
    if (title) return title
  }
  return null
}

export function useConversationTitle(
  agentId: string,
  conversationId: string,
  fallbackAgentName?: string | null,
  options: { readonly detailEnabled?: boolean } = {},
): string | undefined {
  const queryClient = useQueryClient()
  const listTitle = findConversationTitle(
    queryClient.getQueryData<Conversation[]>(conversationKeys.list(agentId)),
    conversationId,
  )
  const pageTitle = findTitleInPages(
    queryClient.getQueriesData<InfiniteData<ConversationListEnvelope>>({
      queryKey: conversationKeys.agentPagesRoot(agentId),
    }),
    conversationId,
  )
  const globalTitle = findTitleInPages(
    queryClient.getQueriesData<InfiniteData<ConversationWithAgentListEnvelope>>({
      queryKey: conversationKeys.globalPagesRoot,
    }),
    conversationId,
  )
  const cachedTitle = listTitle ?? pageTitle ?? globalTitle
  const detail = useConversationDetail(
    conversationId,
    (options.detailEnabled ?? true) && !cachedTitle,
  )
  return cachedTitle ?? detail.data?.title ?? fallbackAgentName ?? undefined
}
