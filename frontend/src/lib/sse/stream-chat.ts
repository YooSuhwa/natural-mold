import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost } from './parse-sse'

export interface StreamChatOptions {
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
  ) as AsyncGenerator<SSEEvent>
}
