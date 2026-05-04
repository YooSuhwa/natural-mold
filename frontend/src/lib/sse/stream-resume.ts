import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost, type StreamSSEPostOptions } from './parse-sse'

export async function* streamResume(
  conversationId: string,
  response: unknown,
  signal?: AbortSignal,
  options?: StreamSSEPostOptions,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/conversations/${conversationId}/messages/resume`,
    { response },
    signal,
    'content_delta',
    options,
  ) as AsyncGenerator<SSEEvent>
}
