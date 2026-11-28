import { independentJobSchema, independentJobListSchema } from '@/lib/types/independent-chat-job'
import { apiFetch } from './client'

function jobsPath(conversationId: string) {
  return `/api/conversations/${encodeURIComponent(conversationId)}/independent-jobs`
}

export const independentJobsApi = {
  async list(conversationId: string, runId: string | null, signal?: AbortSignal) {
    const filter = runId ? `?${new URLSearchParams({ run_id: runId })}` : ''
    return independentJobListSchema.parse(
      await apiFetch<unknown>(jobsPath(conversationId) + filter, { signal }),
    )
  },
  async start(
    conversationId: string,
    runId: string,
    request: {
      request_id: string
      target_id: string
      instruction: string
      source_child_task_id?: string
    },
  ) {
    const path = `/api/conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(runId)}/independent-jobs`
    return independentJobSchema.parse(
      await apiFetch<unknown>(path, { method: 'POST', body: JSON.stringify(request) }),
    )
  },
  async cancel(parentConversationId: string, id: string) {
    const path = `${jobsPath(parentConversationId)}/${encodeURIComponent(id)}/cancel`
    return independentJobSchema.parse(await apiFetch<unknown>(path, { method: 'POST' }))
  },
}
