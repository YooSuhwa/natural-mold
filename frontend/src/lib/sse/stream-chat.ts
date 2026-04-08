import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost } from './parse-sse'

export async function* streamChat(
  conversationId: string,
  content: string,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/conversations/${conversationId}/messages`,
    { content },
    signal,
    'content_delta',
  ) as AsyncGenerator<SSEEvent>
}
