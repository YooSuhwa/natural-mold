import { NotificationsBell } from '@/components/notifications/notifications-bell'
import { UNREAD_POLL_MS } from '@/lib/hooks/use-notifications'
import { notificationQueryKeys } from '@/lib/query-keys/notifications'
import { isRetainedAcrossOrgSwitch } from '@/lib/query-keys/organizations'
import { beginOrgSwitch, finishOrgSwitch } from '@/lib/org-scope/org-scope-signal'
import type { NotificationView, WorkNotificationDestination } from '@/lib/types/notification'
import { render, screen, userEvent, waitFor } from '../../test-utils'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  unreadCount: vi.fn(),
  markRead: vi.fn(),
  markAllRead: vi.fn(),
  destination: vi.fn(),
  resultPath: (id: string) => `/api/notifications/${id}/result`,
}))
const download = vi.hoisted(() => vi.fn())
const errorToast = vi.hoisted(() => vi.fn())
vi.mock('@/lib/hooks/use-api-object-url', () => ({ downloadApiFile: download }))
vi.mock('sonner', () => ({ toast: { error: errorToast } }))
const router = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('@/lib/api/notifications', () => ({ notificationsApi: api }))
vi.mock('next/navigation', () => ({ useRouter: () => router }))
vi.mock('@/lib/hooks/use-organizations', () => ({
  useActiveOrganization: () => ({ activeOrganization: { id: 'org-1' } }),
}))

function item(overrides: Partial<NotificationView>): NotificationView {
  return {
    id: 'n-1',
    kind: 'access_request.created',
    org_id: 'org-1',
    target: { type: 'skill', id: 's-1', name: '영업 리포트', accessible: true },
    actor: { id: 'u-2', display_name: '정우진' },
    payload: { level: 'editor' },
    link: '/settings/organization/requests',
    read_at: null,
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

describe('NotificationsBell', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.markRead.mockResolvedValue(undefined)
    api.markAllRead.mockResolvedValue({ updated: 2 })
  })

  it('shows the capped unread badge in the accessible name and lists kinds', async () => {
    api.unreadCount.mockResolvedValue({ total: 120, by_org: { 'org-1': 120 } })
    api.list.mockResolvedValue({
      items: [
        item({}),
        item({
          id: 'n-2',
          kind: 'trigger.blocked',
          target: { type: 'agent', id: null, name: null, accessible: false },
          actor: null,
          payload: { reason: 'auth_required', trigger_name: '주간 휴가 현황' },
          link: null,
          read_at: '2026-10-17T00:00:00Z',
        }),
      ],
      next_cursor: null,
      has_more: false,
    })
    render(<NotificationsBell />)
    const bell = await screen.findByRole('button', { name: '알림, 안읽음 120개' })
    expect(bell).toHaveTextContent('99+')
    await userEvent.click(bell)
    expect(
      await screen.findByText('정우진 님이 영업 리포트 편집 권한을 요청했어요'),
    ).toBeInTheDocument()
    expect(screen.getByText('주간 휴가 현황 스케줄이 멈췄어요 (인증 필요)')).toBeInTheDocument()
    expect(api.list).toHaveBeenCalledWith(20)
  })

  it('marks a row read, follows its link, and never links hidden targets', async () => {
    api.unreadCount.mockResolvedValue({ total: 1, by_org: {} })
    api.list.mockResolvedValue({
      items: [
        item({
          kind: 'grant.received',
          target: { type: 'tool', id: null, name: null, accessible: false },
          payload: { level: 'consumer' },
          link: null,
        }),
      ],
      next_cursor: null,
      has_more: false,
    })
    render(<NotificationsBell />)
    await userEvent.click(await screen.findByRole('button', { name: '알림, 안읽음 1개' }))
    const row = await screen.findByText('공유받은 항목이 있지만 지금은 열 수 없어요')
    await userEvent.click(row)
    await waitFor(() => expect(api.markRead).toHaveBeenCalled())
    expect(api.markRead.mock.calls[0][0]).toBe('n-1')
    expect(router.push).not.toHaveBeenCalled()
  })

  it('marks all read and shows the empty state', async () => {
    api.unreadCount.mockResolvedValue({ total: 2, by_org: {} })
    api.list.mockResolvedValue({ items: [], next_cursor: null, has_more: false })
    render(<NotificationsBell />)
    await userEvent.click(await screen.findByRole('button', { name: '알림, 안읽음 2개' }))
    expect(await screen.findByText('새 알림이 없어요')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('menuitem', { name: '모두 읽음' }))
    await waitFor(() => expect(api.markAllRead).toHaveBeenCalled())
  })

  const completed: WorkNotificationDestination = {
    agent_id: 'agent-1',
    conversation_id: 'convo-1',
    run_id: 'verified-run',
    goal_id: 'goal-1',
    job_id: null,
    artifact_id: 'artifact-1',
    version_id: 'older-v1',
    filename: '비교표.csv',
  }

  async function openWorkNotice(kind: string, accessible = true) {
    api.unreadCount.mockResolvedValue({ total: 1, by_org: {} })
    api.list.mockResolvedValue({
      items: [
        item({
          kind,
          target: { type: 'work', id: null, name: null, accessible },
          link: '/ignored',
        }),
      ],
      has_more: false,
      next_cursor: null,
    })
    render(<NotificationsBell />)
    await userEvent.click(await screen.findByRole('button', { name: '알림, 안읽음 1개' }))
    const row = await screen.findByRole('menuitem', { name: /^안읽음/ })
    await userEvent.click(row)
    await waitFor(() => expect(api.markRead).toHaveBeenCalled())
  }

  it('opens the verified result from a recipient-scoped endpoint before returning to the goal', async () => {
    api.destination.mockResolvedValue(completed)
    download.mockResolvedValue(undefined)
    await openWorkNotice('chat_goal.completed')
    expect(api.destination).toHaveBeenCalledWith('n-1')
    await waitFor(() =>
      expect(download).toHaveBeenCalledWith(
        '/api/notifications/n-1/result',
        '비교표.csv',
        expect.any(AbortSignal),
      ),
    )
    expect(router.push).toHaveBeenCalledWith(
      '/agents/agent-1/conversations/convo-1#conversation-goal',
    )
  })

  it.each([
    ['chat_goal.blocked', 'goal-1', 'convo-1#conversation-goal'],
    ['chat_task.completed', null, 'convo-1'],
  ])('opens %s without fetching an unrelated file', async (kind, goalId, tail) => {
    api.destination.mockResolvedValue({
      ...completed,
      goal_id: goalId,
      artifact_id: null,
      version_id: null,
    })
    await openWorkNotice(String(kind))
    await waitFor(() =>
      expect(router.push).toHaveBeenCalledWith(`/agents/agent-1/conversations/${tail}`),
    )
    expect(download).not.toHaveBeenCalled()
  })

  it.each(['destination', 'result'])(
    'handles access revoked at the %s without navigating',
    async (stage) => {
      if (stage === 'destination') api.destination.mockRejectedValue(new Error('Not found'))
      else {
        api.destination.mockResolvedValue(completed)
        download.mockRejectedValue(new Error('Not found'))
      }
      await openWorkNotice('chat_goal.completed')
      await waitFor(() => expect(errorToast).toHaveBeenCalledWith('이 알림의 대상을 열 수 없어요'))
      expect(router.push).not.toHaveBeenCalled()
      if (stage === 'destination') expect(download).not.toHaveBeenCalled()
    },
  )

  it('does not follow a cached link for a hidden work target', async () => {
    await openWorkNotice('chat_goal.completed', false)
    expect(api.destination).not.toHaveBeenCalled()
    expect(download).not.toHaveBeenCalled()
    expect(router.push).not.toHaveBeenCalled()
  })

  it('downloads an authorized historical file after the agent is purged', async () => {
    api.destination.mockResolvedValue({ ...completed, agent_id: null })
    download.mockResolvedValue(undefined)
    await openWorkNotice('chat_goal.completed')
    await waitFor(() => expect(download).toHaveBeenCalled())
    expect(router.push).not.toHaveBeenCalled()
    expect(errorToast).not.toHaveBeenCalled()
  })

  it('drops a destination response from the previous organization', async () => {
    api.destination.mockImplementation(async () => {
      beginOrgSwitch()
      finishOrgSwitch()
      return completed
    })
    await openWorkNotice('chat_goal.completed')
    expect(download).not.toHaveBeenCalled()
    expect(router.push).not.toHaveBeenCalled()
    expect(errorToast).not.toHaveBeenCalled()
  })

  it('polls every minute and is purged on an organization switch', () => {
    expect(UNREAD_POLL_MS).toBe(60_000)
    expect(isRetainedAcrossOrgSwitch(notificationQueryKeys.unread('org-1'))).toBe(false)
    expect(notificationQueryKeys.list('org-1')).toEqual(['notifications', 'org-1', 'list'])
  })
})
