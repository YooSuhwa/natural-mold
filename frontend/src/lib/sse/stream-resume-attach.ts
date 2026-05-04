import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEGetResume } from './parse-sse'

/**
 * GET ``/api/conversations/{id}/stream`` 으로 끊긴 SSE stream 을 재개. ``run_id``
 * 는 primary stream 의 ``X-Run-Id`` 응답 헤더에서 받은 값. ``withAutoResume`` 의
 * ``resumeFactory`` 로 넘기는 것이 표준 사용. 서버 분기 명세는 백엔드
 * ``conversations.py:stream_resume`` 참조.
 */
export async function* streamResumeAttach(
  conversationId: string,
  runId: string,
  lastEventId: string | undefined,
  signal?: AbortSignal,
  onMode?: (info: { mode: 'live' | 'replay' | string; runId: string | null }) => void,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEGetResume<SSEEventType>(
    `/api/conversations/${conversationId}/stream`,
    signal,
    {
      runId,
      lastEventId,
      onMode,
      defaultEvent: 'content_delta',
    },
  ) as AsyncGenerator<SSEEvent>
}
