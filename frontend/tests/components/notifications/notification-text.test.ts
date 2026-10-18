import { createTranslator } from 'next-intl'

import { notificationText } from '@/components/notifications/notification-text'
import type { NotificationView } from '@/lib/types/notification'
import ko from '../../../messages/ko.json'

const t = createTranslator({
  locale: 'ko',
  messages: ko,
  namespace: 'notifications',
}) as unknown as Parameters<typeof notificationText>[1]

function grant(overrides: Partial<NotificationView>): NotificationView {
  return {
    id: 'n-1',
    kind: 'grant.received',
    org_id: 'org-1',
    target: { type: 'agent', id: 'a-1', name: '주간 영업 리포트 봇', accessible: true },
    actor: { id: 'u-1', display_name: '박서준' },
    payload: { level: 'viewer' },
    link: null,
    read_at: null,
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

describe('notificationText access_request.created', () => {
  it('reads naturally with and without an actor', () => {
    const request = grant({ kind: 'access_request.created', payload: { level: 'editor' } })
    expect(notificationText(request, t)).toBe(
      '박서준 님이 주간 영업 리포트 봇 편집 권한을 요청했어요',
    )
    const anonymous = notificationText({ ...request, actor: null }, t)
    expect(anonymous).toBe('주간 영업 리포트 봇 편집 권한 요청이 들어왔어요')
    expect(anonymous).not.toMatch(/누군가 님이/)
  })
})

describe('notificationText grant.received', () => {
  it('names the sharer and the item once the recipient can see it', () => {
    expect(notificationText(grant({}), t)).toBe(
      '박서준 님이 주간 영업 리포트 봇의 보기 권한을 공유했어요',
    )
  })

  it('reads naturally without an actor or a visible target', () => {
    expect(notificationText(grant({ actor: null }), t)).toBe(
      '주간 영업 리포트 봇의 보기 권한을 공유받았어요',
    )
    const hidden = notificationText(
      grant({ actor: null, target: { type: 'agent', id: null, name: null, accessible: false } }),
      t,
    )
    expect(hidden).toBe('공유받은 항목이 있지만 지금은 열 수 없어요')
    for (const line of [hidden, notificationText(grant({ actor: null }), t)]) {
      expect(line).not.toMatch(/누군가 님이|을\(를\)/)
    }
  })
})
