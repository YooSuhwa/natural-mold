import { notificationsApi } from '@/lib/api/notifications'

const fetchMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api/client', () => ({ apiFetch: fetchMock }))

describe('notificationsApi', () => {
  beforeEach(() => fetchMock.mockReset().mockResolvedValue(undefined))

  it('resolves each result when opened and encodes the row ID in both endpoints', async () => {
    await notificationsApi.destination('a/b')
    expect(fetchMock.mock.calls).toEqual([
      ['/api/notifications/a%2Fb/destination', { cache: 'no-store' }],
    ])
    expect(notificationsApi.resultPath('a/b')).toBe('/api/notifications/a%2Fb/result')
  })

  it('calls the H-02 routes', async () => {
    await notificationsApi.list()
    await notificationsApi.unreadCount()
    await notificationsApi.markRead('a/b')
    await notificationsApi.markAllRead()
    expect(fetchMock.mock.calls).toEqual([
      ['/api/notifications?limit=20'],
      ['/api/notifications/unread-count'],
      ['/api/notifications/a%2Fb/read', { method: 'POST' }],
      ['/api/notifications/read-all', { method: 'POST' }],
    ])
  })
})
