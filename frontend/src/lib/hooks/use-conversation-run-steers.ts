'use client'

import { useEffect, useRef, useState } from 'react'
import { useAui, useAuiState } from '@assistant-ui/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import {
  conversationRunSteersApi,
  RUN_STEER_PAGE_SIZE,
  type RunSteerReceipt,
  type RunSteerRequest,
} from '@/lib/api/conversation-run-steers'
import { ApiError } from '@/lib/api/errors'
import { useOptionalServerMessageQueueController } from '@/lib/chat/message-queue/server-message-queue-context'
import { conversationRunKeys } from '@/lib/hooks/use-conversation-runs'
import { conversationQueryKeys } from '@/lib/query-keys/conversations'

const REFRESH_MS = 1500
export const MAX_STEER_CHARS = 12_000

export function useRunSteerHistory(conversationId: string) {
  const isRunning = useAuiState((state) => state.thread.isRunning)
  const [cursors, setCursors] = useState<readonly number[]>([])
  const before = cursors.at(-1)
  const latest = before === undefined
  const history = useQuery({
    queryKey: latest
      ? conversationQueryKeys.steers(conversationId)
      : conversationQueryKeys.steersBefore(conversationId, before),
    queryFn: ({ signal }) =>
      latest
        ? conversationRunSteersApi.list(conversationId, signal)
        : conversationRunSteersApi.listBefore(conversationId, before, signal),
    select: (rows) => Array.from(new Map(rows.map((row) => [row.id, row])).values()),
    refetchInterval: (query) =>
      query.state.data?.some((row) => row.status === 'pending')
        ? REFRESH_MS
        : latest && isRunning && process.env.NEXT_PUBLIC_CHAT_STEER_ENABLED === 'true'
          ? 10_000
          : false,
    refetchOnWindowFocus: true,
    retry: false,
  })
  const inaccessible =
    history.error instanceof ApiError && [401, 403, 404].includes(history.error.status)
  const queue = useOptionalServerMessageQueueController()
  const queuedInputs =
    latest && !inaccessible
      ? history.data
          ?.filter((item) => item.status === 'queued')
          .map((item) => item.queued_input_id)
          .join(',')
      : undefined
  useEffect(() => {
    // Without a manually queued turn, the existing queue observer is idle.
    // Refresh it once when a rolled instruction becomes a real queue entry.
    if (queuedInputs) void queue?.refresh()
  }, [queue, queuedInputs])
  const oldest = history.data?.[0]?.sequence
  const hasOlder =
    oldest !== undefined && oldest > 1 && (history.data?.length ?? 0) >= RUN_STEER_PAGE_SIZE
  const older = () => {
    if (hasOlder) setCursors([...cursors, oldest])
  }
  const newer = () => setCursors(cursors.slice(0, -1))
  const recent = () => setCursors([])
  return { history, inaccessible, hasOlder, older, newer, recent, pageDepth: cursors.length }
}

function isAmbiguous(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status >= 500 || error.status === 408
}

export function useRunSteerSubmission(conversationId: string, hasResourceContext: boolean) {
  const aui = useAui()
  const client = useQueryClient()
  const isRunning = useAuiState((state) => state.thread.isRunning)
  const draft = useAuiState((state) => state.composer.text)
  const hasAttachments = useAuiState((state) => state.composer.attachments.length > 0)
  const hasDraft = draft.trim().length > 0
  const active = useQuery({
    queryKey: conversationRunKeys.active(conversationId),
    queryFn: () => conversationRunsApi.active(conversationId),
    enabled: isRunning && hasDraft,
    refetchInterval: isRunning && hasDraft ? REFRESH_MS : false,
    retry: false,
  })
  const busy = useRef(false)
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const receiptKey = conversationQueryKeys.steers(conversationId)
  const submit = useMutation({
    mutationFn: async (request: RunSteerRequest) => {
      try {
        return await conversationRunSteersApi.submit(conversationId, request)
      } catch (error) {
        // A lost HTTP response can still have a committed receipt. A retry
        // must identify the same request and the same original run.
        if (isAmbiguous(error)) {
          const rows = await conversationRunSteersApi.list(conversationId).catch(() => [])
          const received = rows.find(
            (row) => row.client_request_id === request.requestId && row.run_id === request.runId,
          )
          if (received) return received
        }
        throw error
      }
    },
    onSuccess: async (row, request) => {
      if (!mounted.current) return
      await client.cancelQueries({ queryKey: receiptKey, exact: true })
      if (!mounted.current) return
      client.setQueryData<RunSteerReceipt[]>(receiptKey, (rows = []) => {
        const existing = rows.find((item) => item.id === row.id)
        if (existing && existing.status !== 'pending') return rows
        return [...rows.filter((item) => item.id !== row.id), row]
          .sort((a, b) => a.sequence - b.sequence)
          .slice(-RUN_STEER_PAGE_SIZE)
      })
      void client.invalidateQueries({ queryKey: receiptKey, exact: true })
      if (aui.composer.getState().text === request.text) aui.composer.setText('')
    },
    onError: () => {
      if (!mounted.current) return
      void client.invalidateQueries({ queryKey: conversationRunKeys.active(conversationId) })
      void client.invalidateQueries({ queryKey: receiptKey })
    },
    onSettled: () => {
      busy.current = false
    },
  })
  const activeRun = active.data
  // A running resume retains the resolved interrupt ID. Pending approvals are
  // checked by the admission endpoint under the conversation lock.
  const canSteer =
    activeRun?.conversation_id === conversationId &&
    !activeRun.cancel_requested_at &&
    (activeRun.status === 'running' || activeRun.status === 'queued')
  const retryOriginal =
    submit.isError && isAmbiguous(submit.error) && submit.variables.text === draft

  const textOnly = !hasAttachments && !hasResourceContext
  const overLimit = Array.from(draft.trim()).length > MAX_STEER_CHARS
  const disabled =
    (!retryOriginal && (!isRunning || !canSteer)) ||
    submit.isPending ||
    !textOnly ||
    !draft.trim() ||
    overLimit
  function send(): void {
    if (disabled || busy.current) return
    const request = retryOriginal ? submit.variables : null
    const targetRunId = request?.runId ?? (canSteer ? activeRun?.id : undefined)
    if (!targetRunId) return
    const next = request ?? { runId: targetRunId, text: draft, requestId: crypto.randomUUID() }
    busy.current = true
    submit.mutate(next)
  }
  const error = submit.error
  const errorMessage =
    !submit.isError || submit.variables.text !== draft
      ? null
      : error instanceof ApiError && error.code === 'STEER_APPROVAL_PENDING'
        ? 'approvalPending'
        : error instanceof ApiError && error.status === 404
          ? 'unavailable'
          : error instanceof ApiError && error.code === 'STEER_RUN_INACTIVE'
            ? 'inactive'
            : 'failed'
  return {
    send,
    disabled,
    visible: isRunning || retryOriginal,
    retryOriginal,
    textOnly,
    overLimit,
    errorMessage,
  } as const
}
