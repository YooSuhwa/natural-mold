import { API_BASE } from '@/lib/api/client'
import type { BuilderSSEEvent, BuilderSSEEventType } from '@/lib/types'
import { parseSSEStream } from './parse-sse'

export async function* streamBuilder(
  sessionId: string,
  signal?: AbortSignal,
): AsyncGenerator<BuilderSSEEvent> {
  const response = await fetch(`${API_BASE}/api/builder/${sessionId}/stream`, {
    method: 'GET',
    headers: { Accept: 'text/event-stream' },
    signal,
  })

  if (!response.ok) {
    throw new Error(`Builder stream failed: ${response.status}`)
  }

  if (!response.body) {
    throw new Error('No response body')
  }

  for await (const raw of parseSSEStream<BuilderSSEEventType>(response.body, 'phase_progress')) {
    yield { event: raw.event, data: raw.data } as BuilderSSEEvent
  }
}
