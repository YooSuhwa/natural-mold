import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost } from './parse-sse'

export async function* streamResume(
  conversationId: string,
  response: unknown,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/conversations/${conversationId}/messages/resume`,
    { response },
    signal,
    'content_delta',
  ) as AsyncGenerator<SSEEvent>
}
