import { z } from 'zod'

import { apiFetch } from './client'

const runInputStatusSchema = z.enum(['pending', 'claimed', 'canceled', 'failed'])
const jsonObjectSchema = z.record(z.string(), z.json())

const conversationRunInputSchema = z
  .object({
    id: z.string().min(1),
    conversation_id: z.string().min(1),
    run_id: z.string().min(1).nullable(),
    client_request_id: z.string().min(1),
    source: z.string().min(1),
    status: runInputStatusSchema,
    priority: z.number().int(),
    position: z.number().int().positive(),
    revision: z.number().int().positive(),
    input_payload: jsonObjectSchema,
    attachment_ids: z.array(z.string().min(1)).readonly(),
    checkpoint_id: z.string().nullable(),
    claimed_at: z.string().nullable(),
    created_at: z.string().min(1),
    updated_at: z.string().min(1),
  })
  .readonly()

const conversationRunInputListSchema = z
  .object({
    queue_paused: z.boolean(),
    items: z.array(conversationRunInputSchema).readonly(),
  })
  .readonly()

export type ConversationRunInput = z.infer<typeof conversationRunInputSchema>
export type ConversationRunInputList = z.infer<typeof conversationRunInputListSchema>
export type JsonValue = z.infer<ReturnType<typeof z.json>>
export type ConversationRunInputEdit = {
  readonly expectedRevision: number
  readonly input: Readonly<Record<string, JsonValue>>
}
export type ConversationRunInputRevision = {
  readonly id: string
  readonly revision: number
}

function conversationQueuePath(conversationId: string): string {
  return `/api/conversations/${encodeURIComponent(conversationId)}/run-inputs`
}

function conversationQueueItemPath(conversationId: string, inputId: string): string {
  return `${conversationQueuePath(conversationId)}/${encodeURIComponent(inputId)}`
}

async function parseItem(response: Promise<unknown>): Promise<ConversationRunInput> {
  return conversationRunInputSchema.parse(await response)
}

async function parseList(response: Promise<unknown>): Promise<ConversationRunInputList> {
  return conversationRunInputListSchema.parse(await response)
}

export const conversationRunInputsApi = {
  list: (conversationId: string): Promise<ConversationRunInputList> =>
    parseList(apiFetch<unknown>(conversationQueuePath(conversationId))),
  edit: (
    conversationId: string,
    inputId: string,
    request: ConversationRunInputEdit,
  ): Promise<ConversationRunInput> =>
    parseItem(
      apiFetch<unknown>(conversationQueueItemPath(conversationId, inputId), {
        method: 'PATCH',
        body: JSON.stringify({
          expected_revision: request.expectedRevision,
          input: request.input,
        }),
      }),
    ),
  remove: (
    conversationId: string,
    inputId: string,
    expectedRevision: number,
  ): Promise<ConversationRunInput> =>
    parseItem(
      apiFetch<unknown>(
        `${conversationQueueItemPath(conversationId, inputId)}?expected_revision=${expectedRevision}`,
        { method: 'DELETE' },
      ),
    ),
  reorder: (
    conversationId: string,
    items: readonly ConversationRunInputRevision[],
  ): Promise<readonly ConversationRunInput[]> =>
    apiFetch<unknown>(`${conversationQueuePath(conversationId)}/reorder`, {
      method: 'POST',
      body: JSON.stringify({
        ordered_input_ids: items.map((item) => item.id),
        expected_revisions: Object.fromEntries(items.map((item) => [item.id, item.revision])),
      }),
    }).then((response) => z.array(conversationRunInputSchema).readonly().parse(response)),
  promote: (
    conversationId: string,
    inputId: string,
    expectedRevision: number,
  ): Promise<ConversationRunInput> =>
    parseItem(
      apiFetch<unknown>(`${conversationQueueItemPath(conversationId, inputId)}/promote`, {
        method: 'POST',
        body: JSON.stringify({ expected_revision: expectedRevision }),
      }),
    ),
  resume: (conversationId: string): Promise<ConversationRunInputList> =>
    parseList(
      apiFetch<unknown>(`${conversationQueuePath(conversationId)}/resume`, { method: 'POST' }),
    ),
} as const
