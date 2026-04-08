import { API_BASE } from '@/lib/api/client'

/**
 * Shared SSE stream parsing utility.
 * Extracts event/data pairs from a ReadableStream following the SSE protocol.
 */
export async function* parseSSEStream<TEvent extends string>(
  body: ReadableStream<Uint8Array>,
  defaultEvent: TEvent,
): AsyncGenerator<{ event: TEvent; data: unknown }> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent: TEvent = defaultEvent

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim() as TEvent
        } else if (line.startsWith('data: ')) {
          try {
            const data: unknown = JSON.parse(line.slice(6))
            yield { event: currentEvent, data }
          } catch {
            // Skip malformed JSON lines
          }
        }
      }
    }
  } finally {
    reader.cancel().catch(() => {})
  }
}

/**
 * Generic POST-based SSE stream. Sends a JSON body to the given path and
 * yields parsed SSE events.
 */
export async function* streamSSEPost<TEvent extends string>(
  path: string,
  body: Record<string, unknown>,
  signal: AbortSignal | undefined,
  defaultEvent: TEvent,
): AsyncGenerator<{ event: TEvent; data: unknown }> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })

  if (!response.ok) {
    throw new Error(`Stream failed: ${response.status}`)
  }

  if (!response.body) {
    throw new Error('No response body')
  }

  yield* parseSSEStream<TEvent>(response.body, defaultEvent)
}
