import {
  fetchEventSource,
  type EventSourceMessage,
} from '@microsoft/fetch-event-source'
import { API_BASE, fireSessionExpired } from '@/lib/api/client'
import { ApiError, readApiErrorBody } from '@/lib/api/errors'
import { getCsrfToken } from '@/lib/auth/csrf'

/** Error thrown when a stream POST/GET returns a non-2xx with a parseable
 *  ``{error: {code, message}}`` body. Carries the structured fields so the
 *  chat runtime (``useChatRuntime``) can render an inline assistant-side
 *  message for actionable codes (e.g. ``llm_credential_required``) instead
 *  of a generic toast.
 *
 *  Subclasses ``ApiError`` so ``instanceof ApiError`` catches both REST
 *  and SSE failures uniformly (auth-errors / session reload paths).
 *  ``StreamHttpError`` (legacy GET resume path) stays intact for callers
 *  that only need status. */
export class StreamApiError extends ApiError {
  constructor(status: number, code: string | null, message: string) {
    super(status, code ?? 'UNKNOWN_STREAM_ERROR', message)
    this.name = 'StreamApiError'
  }
}

async function readStreamErrorBody(
  response: Response,
): Promise<{ code: string | null; message: string }> {
  const { code, message } = await readApiErrorBody(response, {
    fallbackCode: 'UNKNOWN_STREAM_ERROR',
    fallbackMessage: `요청이 거부되었습니다 (HTTP ${response.status})`,
    clone: true,
  })
  return { code: code === 'UNKNOWN_STREAM_ERROR' ? null : code, message }
}

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
        // SSE 표준: ``field:value`` — colon 뒤의 공백 1개까지 trim. ``id: x`` /
        // ``id:x`` 둘 다 유효. 일부 프록시가 공백을 제거하면 startsWith 매칭이
        // 깨져 lastEventId 추적이 망가졌었음. colon-split 로 통일.
        const colon = line.indexOf(':')
        if (colon < 0) continue  // comment 라인 (``: ...``) 또는 빈 줄
        const field = line.slice(0, colon)
        const rawValue = line.slice(colon + 1)
        const value = rawValue.startsWith(' ') ? rawValue.slice(1) : rawValue
        if (field === 'event') {
          currentEvent = value.trim() as TEvent
        } else if (field === 'id') {
          currentId = value.trim()
        } else if (field === 'data') {
          try {
            const data: unknown = JSON.parse(value)
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
 * POST 기반 SSE stream 옵션.
 *
 * - ``onRunId`` — W3-out M5. 응답 헤더 ``X-Run-Id`` 가 도착하면 1회 호출. 호출
 *   측은 이 id 를 ref 에 저장해 두었다가 GET ``/stream?run_id=`` 로 재연결한다.
 *   primary stream 이 헤더 없이 끝나면 호출되지 않는다.
 */
export interface StreamSSEPostOptions {
  onRunId?: (runId: string) => void
}

/**
 * Generic POST-based SSE stream. Sends a JSON body to the given path and
 * yields parsed SSE events.
 *
 * @microsoft/fetch-event-source 기반:
 * - ``openWhenHidden: true`` → 탭이 백그라운드로 가도 connection 유지. 기본
 *   EventSource는 브라우저가 throttle하거나 끊지만, fetch-event-source는 fetch
 *   API를 직접 사용해 hidden 상태에서도 살아있다.
 * - 자동 재연결은 **비활성화**. POST 재실행 = 새 LangGraph run = 비용 + 중복.
 *   재연결은 W3-out M5 에서 ``streamSSEGetResume`` (GET) + ``withAutoResume``
 *   (caller 측 generator 데코레이터) 조합으로 달성한다.
 * - id/event/data는 ``EventSourceMessage``에서 직접 받아 ``parseSSEStream`` 우회.
 *
 * generator 인터페이스는 그대로 유지 (caller 전부 호환).
 */
export async function* streamSSEPost<TEvent extends string>(
  path: string,
  body: Record<string, unknown>,
  signal: AbortSignal | undefined,
  defaultEvent: TEvent,
  options?: StreamSSEPostOptions,
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
  // CSRF token is grabbed once per stream — fetchEventSource doesn't retry
  // automatically, so a token rotation mid-stream isn't a concern.
  const csrf = getCsrfToken()

  void fetchEventSource(`${API_BASE}${path}`, {
    method: 'POST',
    // Cross-origin (3000 → 8001) needs explicit ``credentials`` so the
    // HttpOnly auth cookies attach. Without it the backend sees an
    // anonymous request and returns 401.
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
    },
    body: JSON.stringify(body),
    signal,
    openWhenHidden: true,
    async onopen(response) {
      // 라이브러리 기본 onopen은 Content-Type을 엄격 검사한다. 백엔드가
      // text/event-stream을 항상 보내지만 일부 프록시가 변환할 수 있어
      // 자체 검증으로 통일.
      if (response.status === 401) {
        // Fire the global session-expired handler so the user sees the toast
        // + redirect, then bail with a clear error. Stream restart on refresh
        // is intentionally out of scope (LangGraph run cost — see plan).
        fireSessionExpired()
        throw new Error(`Stream failed: 401`)
      }
      if (!response.ok) {
        const { code, message } = await readStreamErrorBody(response)
        throw new StreamApiError(response.status, code, message)
      }
      // W3-out M5 — X-Run-Id 헤더는 stream 재연결 식별자. POST 응답 헤더에서
      // 1회 추출해 caller 에게 전달한다. 헤더가 없으면(legacy/예외 경로) 조용히
      // 패스 — withAutoResume 가 runId 없으면 재연결을 시도하지 않는다.
      // ``Headers.get`` 는 case-insensitive (HTML living standard 보장) 라
      // ``streamSSEGetResume`` 의 ``X-Resume-Mode`` 와 동일하게 single-case
      // lookup. 트랙 종료 retrospective 에서 발견된 비대칭 정리.
      if (options?.onRunId) {
        const runId = response.headers.get('X-Run-Id')
        if (runId) options.onRunId(runId)
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
 * GET 기반 SSE stream — W3-out M5 의 stream resume endpoint 전용.
 *
 * - ``Last-Event-ID`` 헤더로 마지막 받은 event id 를 송신 (서버는 query
 *   ``last_event_id`` 우선, 헤더 fallback 으로 둘 다 지원).
 * - ``X-Run-Id``, ``X-Resume-Mode`` 응답 헤더는 ``onMode`` 콜백으로 caller 에
 *   전달. 관찰성 + dedup 정책 결정 (live → boundary 1개 dedup 필요, replay →
 *   서버가 이미 after_id 이후만 보냄).
 * - 백엔드가 reject 분기 시 (4xx, 특히 ``404 RESUME_NOT_FOUND`` /
 *   ``409 RESUME_INTERRUPT_PENDING``) ``onopen`` 에서 ``StreamHttpError`` 를
 *   throw — ``withAutoResume`` 가 retryable 여부를 status 로 판단한다.
 *
 * native ``fetch`` + ``parseSSEStream`` 으로 구현 (POST helper 의 fetch-event-
 * source 는 자동 재연결 비활성화 외에 GET 에 줄 이점이 없음 + Last-Event-ID
 * 헤더를 쓰면 라이브러리 자체 retry 와 충돌).
 */
export interface StreamSSEGetResumeOptions<TEvent extends string> {
  /** SSE 표준 ``Last-Event-ID`` 헤더로 송신할 마지막 event id. */
  lastEventId?: string
  /** ``run_id`` query param. 서버가 broker / DB row 식별에 사용. */
  runId?: string
  /** 응답 헤더 ``X-Resume-Mode`` 와 ``X-Run-Id`` 를 1회 전달. */
  onMode?: (info: { mode: 'live' | 'replay' | string; runId: string | null }) => void
  defaultEvent: TEvent
}

/** GET 기반 stream 이 4xx/5xx 로 reject 됐을 때 throw 되는 에러.
 *  ``withAutoResume`` 가 ``status >= 500`` / 네트워크 에러만 retry 하는 데 사용. */
export class StreamHttpError extends Error {
  constructor(public status: number, public statusText: string) {
    super(`Stream HTTP ${status} ${statusText}`)
    this.name = 'StreamHttpError'
  }
}

export async function* streamSSEGetResume<TEvent extends string>(
  path: string,
  signal: AbortSignal | undefined,
  options: StreamSSEGetResumeOptions<TEvent>,
): AsyncGenerator<SSEEvent<TEvent>> {
  const url = new URL(`${API_BASE}${path}`)
  if (options.runId) url.searchParams.set('run_id', options.runId)
  if (options.lastEventId) url.searchParams.set('last_event_id', options.lastEventId)

  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  if (options.lastEventId) headers['Last-Event-ID'] = options.lastEventId

  const response = await fetch(url.toString(), {
    method: 'GET',
    credentials: 'include',
    headers,
    signal,
  })
  if (response.status === 401) {
    fireSessionExpired()
    throw new StreamHttpError(401, response.statusText)
  }
  if (!response.ok) {
    const { code, message } = await readStreamErrorBody(response)
    throw new StreamApiError(response.status, code, message)
  }
  if (options.onMode) {
    options.onMode({
      mode: response.headers.get('X-Resume-Mode') ?? 'unknown',
      runId: response.headers.get('X-Run-Id'),
    })
  }
  if (!response.body) {
    throw new Error('Stream response has no body')
  }
  yield* parseSSEStream(response.body, options.defaultEvent)
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
