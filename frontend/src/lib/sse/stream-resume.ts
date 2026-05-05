import type { Decision, SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost, type StreamSSEPostOptions } from './parse-sse'

/**
 * HiTL resume — `{decisions}` body로 LangChain `HITLResponse`를 송신.
 * `decisions.length`는 interrupt의 `action_requests.length`와 일치해야 한다.
 */
export async function* streamResumeDecisions(
  conversationId: string,
  decisions: Decision[],
  signal?: AbortSignal,
  options?: StreamSSEPostOptions,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/conversations/${conversationId}/messages/resume`,
    { decisions },
    signal,
    'content_delta',
    options,
  ) as AsyncGenerator<SSEEvent>
}
