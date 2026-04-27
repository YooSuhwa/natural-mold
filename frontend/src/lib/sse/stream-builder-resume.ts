import type { SSEEvent, SSEEventType } from '@/lib/types'
import { streamSSEPost } from './parse-sse'

/**
 * Builder v3 — interrupt 응답 후 그래프 재개.
 * response 형식 (interrupt type별):
 * - ask_user: string
 * - approval: { approved: boolean, revision_message?: string }
 * - image_choice: { choice: 'skip' | 'generate' }
 * - image_approval: { choice: 'confirm' | 'regenerate' | 'skip', prompt?: string }
 */
export async function* streamBuilderResume(
  sessionId: string,
  response: unknown,
  signal?: AbortSignal,
  displayText?: string,
  interruptId?: string | null,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/builder/${sessionId}/messages/resume`,
    {
      response,
      display_text: displayText,
      interrupt_id: interruptId ?? null,
    },
    signal,
    'content_delta',
  ) as AsyncGenerator<SSEEvent>
}
