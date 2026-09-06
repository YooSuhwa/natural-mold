'use client'

import { useEffect, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/api/client'
import { useConversationRun } from '@/lib/hooks/use-conversation-runs'
import {
  buildPersistedRunSummary,
  messageIdBatchFor,
  runIdForMessage,
  type RunMessageLink,
  type RunSummary,
} from '@/lib/chat/run-summary-model'

interface MessageRunSummaryResult {
  readonly summary: RunSummary | null
  readonly isLoading: boolean
}

const FINALIZATION_LINK_ATTEMPTS = 4
const FINALIZATION_LINK_RETRY_MS = 1000

interface RunMessageLinkLifecycle {
  readonly threadIsRunning: boolean
  readonly terminalGeneration: number
}

export function useMessageRunSummary(
  conversationId: string | null,
  messageId: string | null | undefined,
  visibleMessageIds: readonly string[] = [],
  threadIsRunning = false,
): MessageRunSummaryResult {
  const queryClient = useQueryClient()
  const terminalRunId = runIdForMessage([], messageId)
  const messageIdBatch = messageIdBatchFor(messageId, visibleMessageIds)
  const linkQueryEnabled = Boolean(conversationId && !terminalRunId && messageIdBatch.length > 0)
  const resolutionPhase = threadIsRunning ? 'live' : 'terminal'
  const lifecycleKey = useMemo(
    () => ['conversations', conversationId ?? 'none', 'run-message-links-lifecycle'] as const,
    [conversationId],
  )
  const messageIdentityKey = useMemo(
    () =>
      [
        'conversations',
        conversationId ?? 'none',
        'run-message-link-identity',
        messageId ?? 'none',
      ] as const,
    [conversationId, messageId],
  )
  const initialLifecycle = useMemo<RunMessageLinkLifecycle>(
    () => ({ threadIsRunning, terminalGeneration: 0 }),
    [threadIsRunning],
  )
  const lifecycleQuery = useQuery({
    queryKey: lifecycleKey,
    queryFn: async () => initialLifecycle,
    enabled: false,
    initialData: initialLifecycle,
    staleTime: Number.POSITIVE_INFINITY,
  })
  const lifecycle =
    queryClient.getQueryData<RunMessageLinkLifecycle>(lifecycleKey) ?? lifecycleQuery.data
  const terminalGeneration =
    lifecycle.threadIsRunning && !threadIsRunning
      ? lifecycle.terminalGeneration + 1
      : lifecycle.terminalGeneration

  useEffect(() => {
    if (!conversationId) return
    queryClient.setQueryData<RunMessageLinkLifecycle>(lifecycleKey, (current) => {
      if (!current) return initialLifecycle
      if (current.threadIsRunning === threadIsRunning) return current
      return {
        threadIsRunning,
        terminalGeneration:
          current.threadIsRunning && !threadIsRunning
            ? current.terminalGeneration + 1
            : current.terminalGeneration,
      }
    })
  }, [conversationId, initialLifecycle, lifecycleKey, queryClient, threadIsRunning])

  const linksQuery = useQuery({
    queryKey: [
      'conversations',
      conversationId ?? 'none',
      'run-message-links',
      messageIdBatch,
      resolutionPhase,
      terminalGeneration,
    ],
    queryFn: () => {
      const params = new URLSearchParams()
      for (const id of messageIdBatch) params.append('message_id', id)
      return apiFetch<RunMessageLink[]>(
        `/api/conversations/${conversationId}/run-message-links?${params.toString()}`,
      )
    },
    enabled: linkQueryEnabled,
    placeholderData: (previousLinks, previousQuery) => {
      const previousKey = previousQuery?.queryKey
      return previousKey?.[0] === 'conversations' &&
        previousKey[1] === conversationId &&
        previousKey[2] === 'run-message-links'
        ? previousLinks
        : undefined
    },
    refetchOnWindowFocus: false,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
    refetchInterval: (query) => {
      if (threadIsRunning || runIdForMessage(query.state.data ?? [], messageId)) return false
      const attempts = query.state.dataUpdateCount + query.state.errorUpdateCount
      return attempts < FINALIZATION_LINK_ATTEMPTS ? FINALIZATION_LINK_RETRY_MS : false
    },
  })
  const linkedRunId = runIdForMessage(linksQuery.data ?? [], messageId)
  const placeholderMatchesMessage = Boolean(
    conversationId &&
    messageId &&
    linkedRunId &&
    queryClient.getQueryData<string | null>(messageIdentityKey) === linkedRunId,
  )
  const runId =
    terminalRunId ??
    (linksQuery.isPlaceholderData && !placeholderMatchesMessage ? null : linkedRunId)

  useEffect(() => {
    if (!linksQuery.isSuccess || linksQuery.isPlaceholderData) return
    queryClient.setQueryData<string | null>(messageIdentityKey, linkedRunId)
  }, [
    linkedRunId,
    linksQuery.isPlaceholderData,
    linksQuery.isSuccess,
    messageIdentityKey,
    queryClient,
  ])

  const query = useConversationRun(conversationId ?? '', runId, Boolean(conversationId && runId))
  const run = query.data
  return {
    summary: run ? buildPersistedRunSummary(run.id, run.status, run.metrics) : null,
    isLoading: linksQuery.isLoading || query.isLoading,
  }
}
