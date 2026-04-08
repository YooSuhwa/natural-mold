import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost } from './parse-sse'

export async function* streamAssistant(
  agentId: string,
  content: string,
  signal?: AbortSignal,
  sessionId?: string,
): AsyncGenerator<SSEEvent> {
  const body: Record<string, unknown> = { content }
  if (sessionId) {
    body.session_id = sessionId
  }

  yield* streamSSEPost<SSEEventType>(
    `/api/agents/${agentId}/assistant/message`,
    body,
    signal,
    'content_delta',
  ) as AsyncGenerator<SSEEvent>
}
