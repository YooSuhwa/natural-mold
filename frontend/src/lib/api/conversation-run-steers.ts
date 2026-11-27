import { z } from 'zod'
import { apiFetch } from './client'

const receiptSchema = z.object({
  id: z.string(),
  conversation_id: z.string(),
  run_id: z.string(),
  client_request_id: z.string(),
  sequence: z.number().int().positive(),
  text: z.string(),
  status: z.enum(['pending', 'applied', 'queued', 'needs_review']),
  created_at: z.string(),
  applied_at: z.string().nullable(),
  queued_input_id: z.string().nullable(),
})

export type RunSteerReceipt = z.infer<typeof receiptSchema>

export interface RunSteerRequest {
  readonly runId: string
  readonly text: string
  readonly requestId: string
}

export const RUN_STEER_PAGE_SIZE = 50

export const conversationRunSteersApi = {
  async list(conversationId: string, signal?: AbortSignal): Promise<RunSteerReceipt[]> {
    const path = `/api/conversations/${encodeURIComponent(conversationId)}/steers`
    return z.array(receiptSchema).parse(await apiFetch<unknown>(path, { signal }))
  },
  async listBefore(
    conversationId: string,
    before: number,
    signal?: AbortSignal,
  ): Promise<RunSteerReceipt[]> {
    const query = new URLSearchParams({
      before_sequence: String(before),
      limit: String(RUN_STEER_PAGE_SIZE),
    })
    const path = `/api/conversations/${encodeURIComponent(conversationId)}/steers?${query}`
    return z.array(receiptSchema).parse(await apiFetch<unknown>(path, { signal }))
  },
  async submit(conversationId: string, request: RunSteerRequest): Promise<RunSteerReceipt> {
    const path = `/api/conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(request.runId)}/steers`
    return receiptSchema.parse(
      await apiFetch<unknown>(path, {
        method: 'POST',
        body: JSON.stringify({ client_request_id: request.requestId, text: request.text }),
      }),
    )
  },
}
