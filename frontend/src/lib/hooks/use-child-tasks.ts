'use client'

import { useEffect, useMemo, useRef } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '@/lib/api/errors'
import {
  cancelChildTask,
  getChildTask,
  isActiveChildTask,
  listChildTasks,
  sendChildInstruction,
  waitForChildTask,
  type ChildInstructionRequest,
  type ChildTask,
} from '@/lib/api/child-tasks'
import { currentOrgScope, isOrgScopeCurrent } from '@/lib/org-scope/org-scope-signal'
import { conversationQueryKeys } from '@/lib/query-keys/conversations'

export function isRetryableChildTaskError(error: Error) {
  if (error instanceof ApiError) return error.status === 429 || error.status >= 500
  return error instanceof TypeError || error.name === 'NetworkError'
}

const retries = {
  refetchOnMount: 'always' as const,
  refetchOnWindowFocus: 'always' as const,
  retry: (failures: number, error: Error) => failures < 2 && isRetryableChildTaskError(error),
  retryDelay: (attempt: number) => 1_000 * 2 ** attempt,
}

function pollInterval(error: Error | null, active: boolean) {
  if (!active) return false
  if (!error) return 2_000
  return isRetryableChildTaskError(error) ? 15_000 : false
}

export function useChildTasks(conversationId: string, parentRunning = false) {
  const query = useInfiniteQuery({
    queryKey: conversationQueryKeys.childTasks(conversationId, currentOrgScope().generation),
    queryFn: ({ signal, pageParam }) => listChildTasks(conversationId, signal, pageParam),
    initialPageParam: 0,
    getNextPageParam: (page) => page.next_offset ?? undefined,
    refetchInterval: ({ state }) =>
      pollInterval(
        state.error,
        (parentRunning && state.data?.pages[0]?.enabled !== false) ||
          Boolean(state.data?.pages.some((page) => page.items.some(isActiveChildTask))),
      ),
    ...retries,
  })
  // A failed background refetch can leave TanStack's last successful data in
  // the cache. A previous read cannot authorize showing or targeting a child.
  const error = query.failureReason ?? query.error
  const pages = query.isFetchedAfterMount && !error ? query.data?.pages : undefined
  const tasks = useMemo(() => {
    const byId = new Map<string, ChildTask>()
    for (const page of pages ?? []) {
      // Active tasks can change position between fetching two offset pages.
      for (const task of page.items) {
        if (!byId.has(task.id)) byId.set(task.id, task)
      }
    }
    return Array.from(byId.values())
  }, [pages])
  return {
    tasks,
    enabled: pages?.every((page) => page.enabled),
    allowLegacyDetails: Boolean(
      pages?.length && pages.every((page) => !page.enabled && page.allow_legacy_details === true),
    ),
    isFetched: query.isFetchedAfterMount,
    isPending: !query.isFetchedAfterMount && !error,
    isError: Boolean(error),
    error,
    refetch: query.refetch,
    fetchNextPage: query.fetchNextPage,
    hasNextPage: Boolean(pages) && query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
  }
}

export function useChildTask(conversationId: string, childId: string) {
  const query = useQuery({
    queryKey: conversationQueryKeys.childTask(
      conversationId,
      currentOrgScope().generation,
      childId,
    ),
    queryFn: ({ signal }) => getChildTask(conversationId, childId, signal),
    // The selected panel can remain open after the task finishes. Reauthorize
    // its stored result while it is visible, even with no new parent events.
    refetchInterval: ({ state }) =>
      !state.error && state.data && !isActiveChildTask(state.data)
        ? 30_000
        : pollInterval(state.error, true),
    ...retries,
  })
  const error = query.failureReason ?? query.error
  return {
    data: query.isFetchedAfterMount && !error ? query.data : undefined,
    error,
    isError: Boolean(error),
    isPending: !query.isFetchedAfterMount && !error,
    refetch: query.refetch,
  }
}

export { isActiveChildTask }
export type { ChildInstructionRequest, ChildTask }

export function useChildTaskCommands(conversationId: string, childId: string) {
  const client = useQueryClient()
  const scope = currentOrgScope()
  const waitController = useRef<AbortController | null>(null)
  useEffect(() => () => waitController.current?.abort(), [])

  function refresh() {
    if (isOrgScopeCurrent(scope)) {
      return client.invalidateQueries({
        queryKey: conversationQueryKeys.childTasks(conversationId, scope.generation),
      })
    }
  }

  const send = useMutation({
    mutationFn: (request: ChildInstructionRequest) =>
      sendChildInstruction(conversationId, childId, request),
    onSettled: refresh,
  })
  const stop = useMutation({
    mutationFn: () => cancelChildTask(conversationId, childId),
    onSettled: refresh,
  })
  const wait = useMutation({
    mutationFn: () => {
      waitController.current = new AbortController()
      return waitForChildTask(conversationId, childId, waitController.current.signal)
    },
    onSettled: refresh,
  })
  return { send, stop, wait }
}
