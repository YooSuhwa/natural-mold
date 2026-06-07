import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost, type StreamSSEPostOptions } from './parse-sse'

export interface StreamChatOptions extends StreamSSEPostOptions {
  /** Pre-uploaded attachment ids to link to this message (P1-7). */
  attachmentIds?: string[]
}

export async function* streamChat(
  conversationId: string,
  content: string,
  signal?: AbortSignal,
  options?: StreamChatOptions,
): AsyncGenerator<SSEEvent> {
  const body: Record<string, unknown> = { content }
  if (options?.attachmentIds && options.attachmentIds.length > 0) {
    body.attachments = options.attachmentIds.map((id) => ({ id }))
  }
  yield* streamSSEPost<SSEEventType>(
    `/api/conversations/${conversationId}/messages`,
    body,
    signal,
    'content_delta',
    options,
  ) as AsyncGenerator<SSEEvent>
}

export async function* streamStartConversation(
  agentId: string,
  content: string,
  signal?: AbortSignal,
  options?: StreamChatOptions,
): AsyncGenerator<SSEEvent> {
  const body: Record<string, unknown> = { content }
  if (options?.attachmentIds && options.attachmentIds.length > 0) {
    body.attachments = options.attachmentIds.map((id) => ({ id }))
  }
  yield* streamSSEPost<SSEEventType>(
    `/api/agents/${agentId}/conversations/start`,
    body,
    signal,
    'content_delta',
    options,
  ) as AsyncGenerator<SSEEvent>
}
