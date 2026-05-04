import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost, type StreamSSEPostOptions } from './parse-sse'

/**
 * M-CHAT1b — POST `/messages/edit`. Replaces the named user message and
 * forks a new branch from the checkpoint just before it. Streams the new
 * assistant turn back as SSE.
 */
export async function* streamEdit(
  conversationId: string,
  messageId: string,
  newContent: string,
  signal?: AbortSignal,
  options?: StreamSSEPostOptions,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/conversations/${conversationId}/messages/edit`,
    { message_id: messageId, new_content: newContent },
    signal,
    'content_delta',
    options,
  ) as AsyncGenerator<SSEEvent>
}
