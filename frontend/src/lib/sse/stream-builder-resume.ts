import type { Decision, SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost } from './parse-sse'

/**
 * Builder v3 — interrupt 응답 후 그래프 재개.
 *
 * ADR-012 §Phase 5 — 표준 ``decisions: Decision[]`` wire 단일 형식 (clean break).
 * Builder router 가 ``decisions_to_builder_response`` helper 로 phase 별 native
 * shape (string / approval dict / image dict) 으로 변환한 뒤 graph 에 전달.
 */
export async function* streamBuilderResume(
  sessionId: string,
  decisions: Decision[],
  signal?: AbortSignal,
  displayText?: string,
  interruptId?: string | null,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/builder/${sessionId}/messages/resume`,
    {
      decisions,
      display_text: displayText,
      interrupt_id: interruptId ?? null,
    },
    signal,
    'content_delta',
  ) as AsyncGenerator<SSEEvent>
}
