import { StreamApiError, StreamHttpError } from './parse-sse'

/** withAutoResume 가 lastEventId 추적에 사용하는 최소 인터페이스. parse-sse 의
 *  ``SSEEvent<TEvent>`` 와 lib/types 의 discriminated-union ``SSEEvent`` 양쪽이
 *  ``id?: string`` 을 노출하므로 둘 다 그대로 받을 수 있다. */
export interface IdentifiedEvent {
  id?: string
}

/**
 * W3-out M5 — primary SSE stream 이 비정상 종료(네트워크 끊김 등) 했을 때
 * ``resumeFactory`` 로 GET ``/stream`` 을 다시 attach 해 누락된 event 부터
 * 이어 받는 generator decorator.
 *
 * - lastEventId 추적: primary 가 yield 하는 매 event 의 ``id`` 를 기억해 두었다가
 *   재시도 시 ``resumeFactory(lastEventId, attempt)`` 호출.
 * - retryable 판정:
 *   - ``AbortError`` / ``signal.aborted`` → 재시도 X (사용자 cancel)
 *   - ``StreamHttpError`` 4xx (404 RESUME_NOT_FOUND, 409 INTERRUPT_PENDING 등)
 *     → 재시도 X. caller 에 throw 그대로 전파.
 *   - 그 외 (network error, ``StreamHttpError`` 5xx) → backoff 후 재시도.
 * - boundary dedup: 서버는 ``after_id`` *이후* 만 보내지만 timing race 로 같은
 *   event 가 1개 겹칠 수 있다 (primary 가 publish 직후 끊겼고 broker buffer
 *   에 그대로 남은 케이스). 이 경우 caller 의 ``streamGuard.isDuplicate`` 가
 *   처리하므로 여기서는 별도 dedup 안 함 — id 만 정확히 추적.
 * - 콜백 호출 순서:
 *   - 끊김 감지 → backoff 시작 직전 ``onReconnecting(attempt)``
 *   - 재시도 stream 의 첫 event 수신 → ``onReconnected()``
 *   - maxAttempts 초과 → ``onFailed(error)`` 후 throw
 */
export interface WithAutoResumeOptions {
  signal?: AbortSignal
  /** 재시도 횟수 상한 (primary 1회 + 재시도 maxAttempts 회). 기본 3. */
  maxAttempts?: number
  /** 각 재시도 직전 대기 시간(ms). attempt 가 길이를 초과하면 마지막 값 사용. */
  backoffMs?: number[]
  /** 재시도 직전 1회 호출 (attempt: 1, 2, ...). UI 인디케이터 ON. */
  onReconnecting?: (attempt: number) => void
  /** 재시도 stream 에서 첫 event 도착 시 1회 호출. UI 인디케이터 OFF. */
  onReconnected?: () => void
  /** 모든 재시도 실패 또는 비-retryable 에러 시 1회 호출. */
  onFailed?: (error: Error) => void
}

const DEFAULT_BACKOFF_MS = [500, 1500, 4000]
const DEFAULT_MAX_ATTEMPTS = 3

function isRetryableError(err: unknown, signal?: AbortSignal): boolean {
  if (signal?.aborted) return false
  if (err instanceof DOMException && err.name === 'AbortError') return false
  if (err instanceof StreamApiError && err.status >= 400 && err.status < 500) {
    return err.status === 409 && err.code === 'RUN_ATTACH_RETRY'
  }
  if (err instanceof StreamHttpError && err.status >= 400 && err.status < 500) {
    return false
  }
  return true
}

function backoffFor(attempt: number, schedule: number[]): number {
  const idx = Math.min(attempt - 1, schedule.length - 1)
  return schedule[idx] ?? schedule[schedule.length - 1] ?? 0
}

async function sleepWithAbort(ms: number, signal?: AbortSignal): Promise<void> {
  if (ms <= 0) return
  await new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

export async function* withAutoResume<E extends IdentifiedEvent>(
  primary: () => AsyncGenerator<E>,
  resumeFactory: (lastEventId: string | undefined, attempt: number) => AsyncGenerator<E> | null,
  options: WithAutoResumeOptions = {},
): AsyncGenerator<E> {
  const max = options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS
  const backoff = options.backoffMs ?? DEFAULT_BACKOFF_MS
  const signal = options.signal

  const toError = (e: unknown): Error => (e instanceof Error ? e : new Error(String(e)))

  let lastEventId: string | undefined
  let attempt = 0
  let stream: AsyncGenerator<E> | null = primary()
  let isResumed = false

  while (stream !== null) {
    const current = stream
    stream = null
    try {
      for await (const ev of current) {
        if (isResumed) {
          options.onReconnected?.()
          isResumed = false
          attempt = 0
        }
        if (ev.id) lastEventId = ev.id
        yield ev
      }
      return
    } catch (err) {
      // 비-retryable (4xx, AbortError, 이미 abort 된 signal) → 즉시 종료.
      if (!isRetryableError(err, signal)) {
        options.onFailed?.(toError(err))
        throw err
      }
      attempt += 1
      if (attempt > max) {
        options.onFailed?.(toError(err))
        throw toError(err)
      }
      options.onReconnecting?.(attempt)
      try {
        await sleepWithAbort(backoffFor(attempt, backoff), signal)
      } catch (abortErr) {
        // backoff 도중 abort — 원본 err 를 onFailed 에 전달해 caller 가
        // "왜 끊겼는지" 알 수 있게 하고, 실제 throw 는 abortErr (caller 가
        // AbortController 로 식별 가능).
        options.onFailed?.(toError(err))
        throw abortErr
      }
      const next = resumeFactory(lastEventId, attempt)
      if (next === null) {
        options.onFailed?.(toError(err))
        throw toError(err)
      }
      stream = next
      isResumed = true
    }
  }
}
