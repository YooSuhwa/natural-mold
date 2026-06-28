'use client'

import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAuiState } from '@assistant-ui/react'
import { conversationKeys } from '@/lib/hooks/use-conversations'

/**
 * Refetch the unified `/files` list when a run finishes.
 *
 * A just-sent attachment's `message_id` is only backfilled at run finalize, and
 * inline rendering looks attachments up from `/files` by message id — so without
 * this the attachment wouldn't appear until the 15s staleTime lapsed or a
 * reload. Watching `thread.isRunning` true→false and invalidating the query
 * makes it show right when the assistant finishes (also picks up new generated
 * files for the rail). Must be called inside the assistant-ui thread context.
 */
export function useInvalidateFilesOnRunComplete(conversationId: string | null): void {
  const queryClient = useQueryClient()
  const isRunning = useAuiState((s) => s.thread.isRunning)
  const prevRunning = useRef(isRunning)

  useEffect(() => {
    const wasRunning = prevRunning.current
    prevRunning.current = isRunning
    if (!wasRunning || isRunning || !conversationId) return

    const invalidate = () =>
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.files(conversationId),
      })
    // Invalidate immediately, then once more shortly after: the stream ends on
    // the client a beat before the worker finally-block commits the message_id
    // backfill, so a single refetch can race ahead of it.
    invalidate()
    const timer = setTimeout(invalidate, 2000)
    return () => clearTimeout(timer)
  }, [isRunning, conversationId, queryClient])
}
