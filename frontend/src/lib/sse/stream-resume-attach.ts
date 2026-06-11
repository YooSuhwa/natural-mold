import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamAgUiRunAttach } from '@/lib/ag-ui/chat-run-consumer'
import { streamSSEGetResume } from './parse-sse'

type ChatStreamProtocol = 'moldy_sse' | 'ag_ui'

export function getChatStreamProtocol(): ChatStreamProtocol {
  return process.env.NEXT_PUBLIC_CHAT_STREAM_PROTOCOL === 'ag_ui' ? 'ag_ui' : 'moldy_sse'
}

/**
 * GET ``/api/conversations/{id}/runs/{runId}/stream`` 으로 끊긴 SSE stream 을
 * 재개한다. ``runId`` 는 primary stream 의 ``X-Run-Id`` 응답 헤더 또는
 * ``Conversation.active_run`` 에서 받은 durable run id다.
 */
export async function* streamResumeAttach(
  conversationId: string,
  runId: string,
  lastEventId: string | undefined,
  signal?: AbortSignal,
  onMode?: (info: { mode: 'live' | 'replay' | string; runId: string | null }) => void,
): AsyncGenerator<SSEEvent> {
  if (getChatStreamProtocol() === 'ag_ui') {
    yield* streamAgUiRunAttach(conversationId, runId, lastEventId, signal, onMode)
    return
  }
  yield* streamSSEGetResume<SSEEventType>(
    `/api/conversations/${conversationId}/runs/${runId}/stream`,
    signal,
    {
      runId,
      lastEventId,
      onMode,
      defaultEvent: 'content_delta',
    },
  ) as AsyncGenerator<SSEEvent>
}
