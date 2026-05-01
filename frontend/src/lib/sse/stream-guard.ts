import { createEventDeduper } from './parse-sse'

/**
 * Caller-side guard for SSE stream consumers.
 *
 * 두 가지 race를 차단한다:
 * 1. **Stale events** — 사용자가 빌더 cancel + 재시작이나 Edit/Regenerate fork
 *    같은 식으로 새 stream을 시작했을 때, 이전 stream의 generator가 비동기로
 *    뒤늦게 yield한 chunk가 새 stream의 state를 오염시키지 않게 한다.
 *    AbortController는 fetch 레이어만 끊을 뿐, 이미 buffer에 쌓여 있다 caller로
 *    빠져나온 chunk는 막지 못한다.
 * 2. **Duplicate events** — 같은 ``id``의 chunk가 두 번 도착한 경우 두 번째를
 *    무시한다. 백엔드(``streaming.py``)가 ``{msg_id}-{seq}`` 형식으로 매 chunk
 *    마다 unique id를 발행하므로 dedup이 안전하게 동작.
 *
 * 사용 예:
 *   const guardRef = useRef(createStreamGuard())
 *   const token = guardRef.current.begin()
 *   for await (const ev of stream) {
 *     if (guardRef.current.isStale(token)) return
 *     if (guardRef.current.isDuplicate(ev.id)) continue
 *     // handle ev
 *   }
 */
export interface StreamGuard {
  /** 새 stream 시작 — version을 발급하고 dedup 카운터를 reset. 반환된 token으로
   *  consumer가 자신이 살아있는 stream인지 검증한다. */
  begin(): number
  /** ``begin()``으로 받은 token이 더 이상 가장 최근 version이 아니면 stale. */
  isStale(token: number): boolean
  /** 같은 id의 chunk가 두 번째로 도착했는지. id가 없으면 항상 false (구버전
   *  백엔드 호환). */
  isDuplicate(eventId: string | undefined): boolean
}

export function createStreamGuard(): StreamGuard {
  let version = 0
  const dedup = createEventDeduper()
  return {
    begin() {
      version += 1
      dedup.reset()
      return version
    },
    isStale(token) {
      return token !== version
    },
    isDuplicate(eventId) {
      return dedup.isDuplicate(eventId)
    },
  }
}
