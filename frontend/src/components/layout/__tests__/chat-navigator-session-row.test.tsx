import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChatNavigatorSessionRow } from '../chat-navigator-session-row'
import type { ConversationRowActions } from '@/components/chat/use-conversation-row-actions'
import type { Conversation } from '@/lib/types'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}(${Object.values(params).join('/')})` : key,
}))

vi.mock('@/components/agent/agent-avatar', () => ({
  AgentAvatar: ({ name }: { name: string }) => <span data-testid="avatar">{name}</span>,
}))

vi.mock('@/components/ui/tooltip', () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
  TooltipContent: () => null,
}))

const ACTIONS = {
  openRenameDialog: vi.fn(),
  openShareDialog: vi.fn(),
  openExportDialog: vi.fn(),
  togglePin: vi.fn(),
  requestDelete: vi.fn(),
} as unknown as ConversationRowActions

function makeConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 'conv-1',
    agent_id: 'agent-1',
    title: '아침 브리핑',
    is_pinned: false,
    unread_count: 0,
    last_activity_source: 'user',
    active_run: null,
    updated_at: '2026-07-04T06:00:00Z',
    created_at: '2026-07-04T06:00:00Z',
    ...overrides,
  } as unknown as Conversation
}

function renderRow(conversation: Conversation) {
  return render(
    <ChatNavigatorSessionRow
      conversation={conversation}
      agent={null}
      active={false}
      actions={ACTIONS}
    />,
  )
}

describe('ChatNavigatorSessionRow — 스케줄 활동 배지', () => {
  it('last_activity_source가 schedule이면 시계 배지를 보여준다', () => {
    const { container } = renderRow(
      makeConversation({ last_activity_source: 'schedule', unread_count: 2 }),
    )
    expect(container.querySelector('[data-moldy-schedule-activity="conv-1"]')).not.toBeNull()
  })

  it('일반(user) 대화에는 배지가 없다', () => {
    const { container } = renderRow(makeConversation())
    expect(container.querySelector('[data-moldy-schedule-activity]')).toBeNull()
  })

  it('핀 고정 대화는 핀 아이콘이 우선한다', () => {
    const { container } = renderRow(
      makeConversation({ is_pinned: true, last_activity_source: 'schedule' }),
    )
    expect(container.querySelector('[data-moldy-schedule-activity]')).toBeNull()
  })
})
