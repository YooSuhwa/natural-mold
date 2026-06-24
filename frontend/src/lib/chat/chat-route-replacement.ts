export const CHAT_ROUTE_REPLACED_EVENT = 'moldy:chat-route-replaced'
export const CHAT_ROUTE_CLEARED_EVENT = 'moldy:chat-route-cleared'

export interface ChatRouteReplacedDetail {
  readonly pathname: string
}

export function isChatRouteReplacedEvent(
  event: Event,
): event is CustomEvent<ChatRouteReplacedDetail> {
  if (event.type !== CHAT_ROUTE_REPLACED_EVENT) return false
  const detail = (event as CustomEvent<unknown>).detail
  return (
    typeof detail === 'object' &&
    detail !== null &&
    'pathname' in detail &&
    typeof detail.pathname === 'string'
  )
}

/**
 * draft → real 대화 승격 시 URL만 교체하고 컴포넌트 remount는 피한다
 * (langgraph-v3 전용 경로). Next.js 16 공식 가이드(`docs/.../linking-and-navigating`
 * "Native History API")는 `window.history.replaceState`를 직접 호출하면 Next
 * Router에 통합되어 `usePathname`/`useSearchParams`와 동기화된다고 명시한다.
 *
 * 제약/이전 버그:
 * - 반드시 `window.history.replaceState`를 호출해야 한다. `History.prototype`을
 *   직접 부르면 Next가 monkey-patch한 wrapper를 우회해 App Router 캐시/pathname이
 *   갱신되지 않는다 (그러면 "draft 전송 → 다른 대화 클릭 → 뒤로가기"가 엉뚱한
 *   대화나 `/new`로 돌아간다).
 * - state 인자는 새 URL 기준으로 `null`을 넘긴다. 이전 코드는 OLD URL의
 *   `window.history.state`를 그대로 재사용해 뒤로가기 시 stale state가 복원됐다.
 *   Next 가이드 예시와 동일하게 `null`을 쓴다.
 */
export function replaceChatRouteWithoutRemount(path: string): void {
  if (typeof window === 'undefined') return
  window.history.replaceState(null, '', path)
  const pathname = new URL(path, window.location.href).pathname
  window.dispatchEvent(
    new CustomEvent<ChatRouteReplacedDetail>(CHAT_ROUTE_REPLACED_EVENT, {
      detail: { pathname },
    }),
  )
}

export function clearChatRouteReplacement(): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new Event(CHAT_ROUTE_CLEARED_EVENT))
}

/**
 * `/agents/<agentId>/conversations/<conversationId>` 경로에서 conversationId를
 * 추출한다. agentId가 다르거나 형식이 안 맞으면 null. conversationId는
 * `decodeURIComponent`로 디코드한다(percent-encoded id 대응). 디코드가 실패하면
 * (malformed % sequence) raw 세그먼트를 그대로 돌려준다.
 */
export function conversationIdFromChatPath(pathname: string, agentId: string): string | null {
  const match = /^\/agents\/([^/]+)\/conversations\/([^/]+)$/.exec(pathname)
  if (!match || match[1] !== agentId) return null
  try {
    return decodeURIComponent(match[2])
  } catch {
    return match[2]
  }
}
