import { describe, expect, it } from 'vitest'
import {
  CHAT_ROUTE_CLEARED_EVENT,
  CHAT_ROUTE_REPLACED_EVENT,
  conversationIdFromChatPath,
  isChatRouteReplacedEvent,
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
