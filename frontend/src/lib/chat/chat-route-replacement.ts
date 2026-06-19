export const CHAT_ROUTE_REPLACED_EVENT = 'moldy:chat-route-replaced'

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

export function replaceChatRouteWithoutRemount(path: string): void {
  History.prototype.replaceState.call(window.history, window.history.state, '', path)
  const pathname = new URL(path, window.location.href).pathname
  window.dispatchEvent(
    new CustomEvent<ChatRouteReplacedDetail>(CHAT_ROUTE_REPLACED_EVENT, {
      detail: { pathname },
    }),
  )
}
