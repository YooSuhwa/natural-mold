import type { Decision, SSEEvent, SSEEventType } from '@/lib/types'
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

export async function* streamAssistantResume(
  agentId: string,
  decisions: Decision[],
  signal?: AbortSignal,
  displayText?: string,
  interruptId?: string | null,
  sessionId?: string,
): AsyncGenerator<SSEEvent> {
  const body: Record<string, unknown> = {
    decisions,
    display_text: displayText,
    interrupt_id: interruptId ?? null,
  }
  if (sessionId) {
    body.session_id = sessionId
  }

  yield* streamSSEPost<SSEEventType>(
    `/api/agents/${agentId}/assistant/message/resume`,
    body,
    signal,
    'content_delta',
  ) as AsyncGenerator<SSEEvent>
}
