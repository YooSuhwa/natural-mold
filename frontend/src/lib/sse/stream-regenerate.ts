import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost } from './parse-sse'

/**
 * M-CHAT1b — POST `/messages/regenerate`. Replays the parent user turn from
 * the checkpoint just before the named assistant message (or the latest
 * assistant message if `messageId` is omitted). The new run forks a sibling
 * assistant branch.
 */
export async function* streamRegenerate(
  conversationId: string,
  messageId?: string,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const body: Record<string, unknown> = {}
  if (messageId) body.message_id = messageId
  yield* streamSSEPost<SSEEventType>(
    `/api/conversations/${conversationId}/messages/regenerate`,
    body,
    signal,
    'content_delta',
  ) as AsyncGenerator<SSEEvent>
}
