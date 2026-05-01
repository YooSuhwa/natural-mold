import {
  fetchEventSource,
  type EventSourceMessage,
} from '@microsoft/fetch-event-source'
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
 *
 * @microsoft/fetch-event-source 기반:
 * - ``openWhenHidden: true`` → 탭이 백그라운드로 가도 connection 유지. 기본
 *   EventSource는 브라우저가 throttle하거나 끊지만, fetch-event-source는 fetch
 *   API를 직접 사용해 hidden 상태에서도 살아있다.
 * - 자동 재연결은 **비활성화**. natural-mold의 SSE는 POST 기반이라 같은 stream을
 *   다시 attach할 수 없다 (POST 재실행은 새 LangGraph run = 비용 + 중복). retry는
 *   서버에 GET-based resume endpoint가 추가될 때(W3-out) 풀린다.
 * - id/event/data는 ``EventSourceMessage``에서 직접 받아 ``parseSSEStream`` 우회.
 *   재연결을 안 하므로 ``Last-Event-ID`` 헤더 송신도 불필요.
 *
 * generator 인터페이스는 그대로 유지 (caller 전부 호환).
 */
export async function* streamSSEPost<TEvent extends string>(
  path: string,
  body: Record<string, unknown>,
  signal: AbortSignal | undefined,
  defaultEvent: TEvent,
): AsyncGenerator<SSEEvent<TEvent>> {
  // Callback-driven fetchEventSource → generator로 bridge.
  const buffer: SSEEvent<TEvent>[] = []
  let resolver: (() => void) | null = null
  let terminalError: Error | null = null
  let closed = false

  const wakeUp = () => {
    if (resolver) {
      const r = resolver
      resolver = null
      r()
    }
  }

  // .catch로 fetchEventSource Promise를 처리해 unhandled rejection 방지.
  // 실제 종료 신호는 onclose/onerror에서 closed=true로 표시한다.
  void fetchEventSource(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(body),
    signal,
    openWhenHidden: true,
    async onopen(response) {
      // 라이브러리 기본 onopen은 Content-Type을 엄격 검사한다. 백엔드가
      // text/event-stream을 항상 보내지만 일부 프록시가 변환할 수 있어
      // 자체 검증으로 통일.
      if (!response.ok) {
        throw new Error(`Stream failed: ${response.status}`)
      }
    },
    onmessage(msg: EventSourceMessage) {
      try {
        const data: unknown = JSON.parse(msg.data)
        buffer.push({
          event: ((msg.event || defaultEvent) as TEvent),
          data,
          id: msg.id || undefined,
        })
        wakeUp()
      } catch {
        // malformed JSON — 이전 parseSSEStream과 동일하게 silently skip.
      }
    },
    onclose() {
      closed = true
      wakeUp()
    },
    onerror(err) {
      // POST는 idempotent하지 않으므로 자동 재시도 금지. throw하면
      // fetchEventSource는 retry를 하지 않고 promise를 reject한다.
      terminalError = err instanceof Error ? err : new Error(String(err))
      closed = true
      wakeUp()
      throw err
    },
  }).catch((err) => {
    if (!terminalError && !(err instanceof DOMException && err.name === 'AbortError')) {
      terminalError = err instanceof Error ? err : new Error(String(err))
    }
    closed = true
    wakeUp()
  })

  while (true) {
    if (buffer.length > 0) {
      yield buffer.shift()!
      continue
    }
    if (closed) {
      if (terminalError) throw terminalError
      return
    }
    await new Promise<void>((resolve) => {
      resolver = resolve
    })
  }
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
