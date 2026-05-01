import { API_BASE } from '@/lib/api/client'

/**
 * 단일 SSE 이벤트.
 *
 * - ``event``: ``event:`` 라인 값 (없으면 ``defaultEvent``).
 * - ``data``: ``data:`` 라인의 JSON parsed value.
 * - ``id``: ``id:`` 라인 값 (서버가 발행한 경우만). 클라이언트는 이 값으로
 *   동일 stream 재시도 시 중복 이벤트를 dedup하거나 stale 폐기에 활용한다.
 */
export interface SSEEvent<TEvent extends string> {
  event: TEvent
  data: unknown
  id?: string
}

/**
 * Shared SSE stream parsing utility.
 * Extracts event/id/data triples from a ReadableStream following the SSE protocol.
 */
export async function* parseSSEStream<TEvent extends string>(
  body: ReadableStream<Uint8Array>,
  defaultEvent: TEvent,
): AsyncGenerator<SSEEvent<TEvent>> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent: TEvent = defaultEvent
  let currentId: string | undefined

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
        } else if (line.startsWith('id: ')) {
          currentId = line.slice(4).trim()
        } else if (line.startsWith('data: ')) {
          try {
            const data: unknown = JSON.parse(line.slice(6))
            yield { event: currentEvent, data, id: currentId }
          } catch {
            // Skip malformed JSON lines
          }
          // SSE 표준: id는 같은 message 내부에서만 유효. 다음 message 경계
          // (빈 라인)까지 유지될 수 있지만 자체 stream에서 매 chunk마다 새 id를
          // 발행하므로 매 yield 후 reset해도 안전.
          currentId = undefined
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
): AsyncGenerator<SSEEvent<TEvent>> {
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

/**
 * 같은 stream 내 중복 이벤트를 거르는 단순 dedup helper.
 *
 * 백엔드가 ``id: {msg_id}-{seq}`` 형식으로 매 chunk마다 unique id를 발행하므로,
 * 같은 id가 두 번 도착하면(재연결 후 동일 chunk 재수신 등) 두 번째는 무시할 수
 * 있다. id가 없는 이벤트는 항상 통과 (구버전 백엔드 호환).
 *
 * 사용처:
 *   const dedup = createEventDeduper()
 *   for await (const ev of parseSSEStream(...)) {
 *     if (dedup.isDuplicate(ev.id)) continue
 *     ...
 *   }
 */
export function createEventDeduper(): {
  isDuplicate(id: string | undefined): boolean
  reset(): void
  size(): number
} {
  const seen = new Set<string>()
  return {
    isDuplicate(id) {
      if (!id) return false
      if (seen.has(id)) return true
      seen.add(id)
      return false
    },
    reset() {
      seen.clear()
    },
    size() {
      return seen.size
    },
  }
}
