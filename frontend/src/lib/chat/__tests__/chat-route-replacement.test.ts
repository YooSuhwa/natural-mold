import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CHAT_ROUTE_CLEARED_EVENT,
  CHAT_ROUTE_REPLACED_EVENT,
  clearChatRouteReplacement,
  conversationIdFromChatPath,
  isChatRouteReplacedEvent,
  replaceChatRouteWithoutRemount,
} from '../chat-route-replacement'

describe('conversationIdFromChatPath', () => {
  it('매칭되는 agentId 경로에서 conversationId를 추출한다', () => {
    expect(conversationIdFromChatPath('/agents/agent-1/conversations/conv-9', 'agent-1')).toBe(
      'conv-9',
    )
  })

  it('agentId가 다르면 null을 반환한다', () => {
    expect(conversationIdFromChatPath('/agents/agent-2/conversations/conv-9', 'agent-1')).toBeNull()
  })

  it('채팅 경로 형식이 아니면 null을 반환한다', () => {
    expect(conversationIdFromChatPath('/agents/agent-1/settings', 'agent-1')).toBeNull()
    expect(conversationIdFromChatPath('/agents/agent-1/conversations', 'agent-1')).toBeNull()
    expect(
      conversationIdFromChatPath('/agents/agent-1/conversations/conv-1/traces', 'agent-1'),
    ).toBeNull()
    expect(conversationIdFromChatPath('', 'agent-1')).toBeNull()
    expect(conversationIdFromChatPath('/', 'agent-1')).toBeNull()
  })

  it('percent-encoded conversationId를 디코드한다', () => {
    expect(
      conversationIdFromChatPath('/agents/agent-1/conversations/conv%20a%2Fb', 'agent-1'),
    ).toBe('conv a/b')
  })

  it('percent-encoded agentId도 raw 세그먼트로 비교한다(디코드된 비교가 아님)', () => {
    // route param의 agentId는 보통 이미 디코드된 값이라, raw 세그먼트가 다르면 null.
    expect(
      conversationIdFromChatPath('/agents/agent%201/conversations/conv-1', 'agent 1'),
    ).toBeNull()
    expect(conversationIdFromChatPath('/agents/agent%201/conversations/conv-1', 'agent%201')).toBe(
      'conv-1',
    )
  })

  it('malformed percent sequence는 raw 세그먼트로 폴백한다', () => {
    expect(conversationIdFromChatPath('/agents/agent-1/conversations/%E0%A4%A', 'agent-1')).toBe(
      '%E0%A4%A',
    )
  })
})

describe('isChatRouteReplacedEvent', () => {
  it('올바른 detail.pathname을 가진 CustomEvent를 인식한다', () => {
    const event = new CustomEvent(CHAT_ROUTE_REPLACED_EVENT, {
      detail: { pathname: '/agents/a/conversations/c' },
    })
    expect(isChatRouteReplacedEvent(event)).toBe(true)
  })

  it('이벤트 타입이 다르면 false', () => {
    const event = new CustomEvent('some-other-event', {
      detail: { pathname: '/agents/a/conversations/c' },
    })
    expect(isChatRouteReplacedEvent(event)).toBe(false)
  })

  it('cleared 이벤트는 false', () => {
    expect(isChatRouteReplacedEvent(new Event(CHAT_ROUTE_CLEARED_EVENT))).toBe(false)
  })

  it('detail이 없거나 pathname이 문자열이 아니면 false', () => {
    expect(isChatRouteReplacedEvent(new CustomEvent(CHAT_ROUTE_REPLACED_EVENT))).toBe(false)
    expect(
      isChatRouteReplacedEvent(new CustomEvent(CHAT_ROUTE_REPLACED_EVENT, { detail: {} })),
    ).toBe(false)
    expect(
      isChatRouteReplacedEvent(
        new CustomEvent(CHAT_ROUTE_REPLACED_EVENT, { detail: { pathname: 123 } }),
      ),
    ).toBe(false)
    expect(
      isChatRouteReplacedEvent(
        new CustomEvent(CHAT_ROUTE_REPLACED_EVENT, { detail: { pathname: null } }),
      ),
    ).toBe(false)
  })

  it('detail이 null이면 false', () => {
    expect(
      isChatRouteReplacedEvent(new CustomEvent(CHAT_ROUTE_REPLACED_EVENT, { detail: null })),
    ).toBe(false)
  })
})

describe('replaceChatRouteWithoutRemount', () => {
  beforeEach(() => {
    // jsdom 시작 경로를 알려진 값으로 고정해 상대 경로 해석을 안정화한다.
    window.history.replaceState(null, '', '/agents/agent-1/conversations/new')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('window.history.replaceState를 (null, "", path)로 호출한다', () => {
    const replaceStateSpy = vi.spyOn(window.history, 'replaceState')
    const path = '/agents/agent-1/conversations/conv-42'

    replaceChatRouteWithoutRemount(path)

    // 회귀: state 인자는 반드시 null이어야 한다(OLD URL의 stale state 재사용 금지).
    // 또한 History.prototype이 아닌 window.history.replaceState(Next monkey-patch
    // wrapper)를 거쳐야 App Router 캐시/pathname이 동기화된다.
    expect(replaceStateSpy).toHaveBeenCalledWith(null, '', path)
  })

  it('detail.pathname을 가진 CHAT_ROUTE_REPLACED_EVENT를 dispatch한다', () => {
    const path = '/agents/agent-1/conversations/conv-42'
    const events: CustomEvent<unknown>[] = []
    const listener = (event: Event) => {
      events.push(event as CustomEvent<unknown>)
    }
    window.addEventListener(CHAT_ROUTE_REPLACED_EVENT, listener)
    try {
      replaceChatRouteWithoutRemount(path)
    } finally {
      window.removeEventListener(CHAT_ROUTE_REPLACED_EVENT, listener)
    }

    expect(events).toHaveLength(1)
    const event = events[0]
    expect(event?.type).toBe(CHAT_ROUTE_REPLACED_EVENT)
    expect(isChatRouteReplacedEvent(event as Event)).toBe(true)
    expect((event?.detail as { pathname?: unknown })?.pathname).toBe(path)
  })

  it('쿼리스트링이 붙은 경로에서도 pathname만 detail에 담는다', () => {
    const events: CustomEvent<unknown>[] = []
    const listener = (event: Event) => {
      events.push(event as CustomEvent<unknown>)
    }
    window.addEventListener(CHAT_ROUTE_REPLACED_EVENT, listener)
    try {
      replaceChatRouteWithoutRemount('/agents/agent-1/conversations/conv-42?from=draft')
    } finally {
      window.removeEventListener(CHAT_ROUTE_REPLACED_EVENT, listener)
    }

    expect((events[0]?.detail as { pathname?: unknown })?.pathname).toBe(
      '/agents/agent-1/conversations/conv-42',
    )
  })

  it('SSR(window 미정의)에서는 no-op으로 안전하게 반환한다', () => {
    const original = globalThis.window
    // @ts-expect-error - SSR 환경 시뮬레이션을 위해 window를 일시적으로 제거한다.
    delete globalThis.window
    try {
      expect(() =>
        replaceChatRouteWithoutRemount('/agents/agent-1/conversations/conv-42'),
      ).not.toThrow()
    } finally {
      globalThis.window = original
    }
  })
})

describe('clearChatRouteReplacement', () => {
  it('CHAT_ROUTE_CLEARED_EVENT를 dispatch한다', () => {
    const events: Event[] = []
    const listener = (event: Event) => {
      events.push(event)
    }
    window.addEventListener(CHAT_ROUTE_CLEARED_EVENT, listener)
    try {
      clearChatRouteReplacement()
    } finally {
      window.removeEventListener(CHAT_ROUTE_CLEARED_EVENT, listener)
    }

    expect(events).toHaveLength(1)
    expect(events[0]?.type).toBe(CHAT_ROUTE_CLEARED_EVENT)
    // cleared 이벤트는 replaced 이벤트가 아니다.
    expect(isChatRouteReplacedEvent(events[0] as Event)).toBe(false)
  })

  it('SSR(window 미정의)에서는 no-op으로 안전하게 반환한다', () => {
    const original = globalThis.window
    // @ts-expect-error - SSR 환경 시뮬레이션을 위해 window를 일시적으로 제거한다.
    delete globalThis.window
    try {
      expect(() => clearChatRouteReplacement()).not.toThrow()
    } finally {
      globalThis.window = original
    }
  })
})
