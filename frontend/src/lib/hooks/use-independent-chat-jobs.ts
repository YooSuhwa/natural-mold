'use client'

import { useRef } from 'react'
import { useAtomValue } from 'jotai'
import { skipToken, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { independentJobsApi } from '@/lib/api/independent-chat-jobs'
import { pendingJobRequest } from '@/lib/chat/independent-job-request'
import { currentOrgScope, isOrgScopeCurrent } from '@/lib/org-scope/org-scope-signal'
import { ApiError } from '@/lib/api/errors'
import {
  isOpenIndependentJob,
  type IndependentChatJob,
  type IndependentJobList,
} from '@/lib/types/independent-chat-job'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { conversationRunKeys } from '@/lib/hooks/use-conversation-runs'
import { conversationRuntimeStatusAtom } from '@/lib/stores/chat-navigator-store'

const listKey = (id: string | null) => ['conversations', id, 'independent-jobs'] as const

export function useIndependentChatJobs(
  conversationId: string | null,
  envelopeRunId: string | null,
) {
  const running = useAtomValue(conversationRuntimeStatusAtom)[conversationId ?? ''] === 'running'
  // A newly submitted run may start before its message envelope refreshes.
  const activeRun = useQuery({
    queryKey: conversationRunKeys.active(conversationId ?? ''),
    queryFn: conversationId ? () => conversationRunsApi.active(conversationId) : skipToken,
    enabled: !!conversationId && running,
    refetchInterval: running ? 2000 : false,
  })
  const runId = (running && activeRun.data?.id) || envelopeRunId
  return useIndependentJobListing(conversationId, runId, running)
}

export function useIndependentJobListing(
  conversationId: string | null,
  runId: string | null,
  running = false,
) {
  const client = useQueryClient()
  const key = [...listKey(conversationId), runId]
  const query = useQuery({
    queryKey: key,
    queryFn: conversationId
      ? ({ signal }) => independentJobsApi.list(conversationId, runId, signal)
      : skipToken,
    enabled: !!conversationId,
    refetchInterval: ({ state: { data } }) =>
      data &&
      ((data.enabled && running) ||
        data.jobs.some(isOpenIndependentJob) ||
        (data.parent_job && isOpenIndependentJob(data.parent_job)))
        ? 3000
        : false,
  })

  function applyJob(job: IndependentChatJob) {
    if (!conversationId) return
    client.setQueriesData<IndependentJobList>({ queryKey: listKey(conversationId) }, (previous) => {
      if (!previous) return previous
      if (previous.parent_job?.id === job.id) return { ...previous, parent_job: job }
      if (job.parent_conversation_id !== conversationId) return previous
      return { ...previous, jobs: [job, ...previous.jobs.filter((item) => item.id !== job.id)] }
    })
    void client.invalidateQueries({ queryKey: listKey(conversationId) })
  }

  return { runId, query, applyJob }
}

export function useStartIndependentJob(
  conversationId: string,
  runId: string,
  onJob: (job: IndependentChatJob) => void,
) {
  const pending = useRef(new Map<string, string>())
  const start = useMutation({
    mutationFn: async ({
      targetId,
      instruction,
      childTaskId,
    }: {
      targetId: string
      instruction: string
      childTaskId?: string
    }) => {
      const scope = currentOrgScope()
      const text = instruction.trim()
      const request = await pendingJobRequest(runId, targetId, text, pending.current, childTaskId)
      if (!isOrgScopeCurrent(scope)) throw new DOMException('Organization changed', 'AbortError')
      const job = await independentJobsApi.start(conversationId, runId, {
        request_id: request.id,
        target_id: targetId,
        instruction: text,
        ...(childTaskId ? { source_child_task_id: childTaskId } : {}),
      })
      if (!isOrgScopeCurrent(scope)) throw new DOMException('Organization changed', 'AbortError')
      request.confirm()
      return job
    },
    onSuccess: onJob,
  })
  const ineligible = start.error instanceof ApiError && [403, 404, 409].includes(start.error.status)
  return { start, ineligible }
}

export function useCancelIndependentJob(
  job: IndependentChatJob,
  onJob: (job: IndependentChatJob) => void,
) {
  return useMutation({
    mutationFn: () => independentJobsApi.cancel(job.parent_conversation_id, job.id),
    onSuccess: onJob,
  })
}
