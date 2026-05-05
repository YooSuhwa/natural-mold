import type { Decision, SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost, type StreamSSEPostOptions } from './parse-sse'

/**
 * Legacy resume — `{response}` body 송신.
 *
 * Phase 2 transition window 동안 보존. backend router(`resume_message`)가
 * 단일 respond decision으로 변환 후 표준 미들웨어에 전달한다. Phase 3에서
 * 제거 예정 (단일 진실 공급원: `docs/exec-plans/active/hitl-phase2-contract.md` §6.2).
 *
 * @deprecated Phase 3에서 제거. 신규 호출자는 `streamResumeDecisions` 사용.
 */
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

/**
 * 표준 resume — `{decisions: [...]}` body 송신.
 *
 * `decisions` 배열은 LangChain `HITLResponse.decisions`와 동일 shape.
 * `action_requests.length`와 길이가 같아야 미들웨어가 valid response로 인식.
 * (단일 진실 공급원: `docs/exec-plans/active/hitl-phase2-contract.md` §6.1)
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
