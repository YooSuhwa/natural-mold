import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost } from './parse-sse'

/**
 * Builder v3 — POST 메시지 + SSE 스트림.
 * 첫 메시지: 그래프를 처음부터 실행 (Phase 1).
 * 후속 메시지: messages만 추가 (그래프 진행 중일 때).
 */
export async function* streamBuilderMessage(
  sessionId: string,
  content: string,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/builder/${sessionId}/messages`,
    { content },
    signal,
    'content_delta',
  ) as AsyncGenerator<SSEEvent>
}
