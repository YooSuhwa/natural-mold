import { render, screen, userEvent } from '../../test-utils'
import {
  RecentAgentsSection,
  RecentSessionsSection,
} from '@/components/layout/chat-navigator-sections'
import type { ConversationRowActions } from '@/components/chat/use-conversation-row-actions'
import { mockAgentSummaryList, mockConversation } from '../../mocks/fixtures'

vi.mock('next/link', () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode
    href: string
    [key: string]: unknown
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}))

function createRowActions(): ConversationRowActions {
  return {
    isDeleting: false,
    dialogs: null,
    openRenameDialog: vi.fn(),
    openShareDialog: vi.fn(),
    requestDelete: vi.fn(),
    togglePin: vi.fn(),
  }
}

describe('collapsed chat navigator controls', () => {
  it('hides recent agents text pagination controls in the icon rail', async () => {
    const user = userEvent.setup()
    const onExpandSidebar = vi.fn()
    const baseAgent = mockAgentSummaryList[0]
    if (!baseAgent) {
      throw new TypeError('agent fixture was missing')
    }
    const agents = Array.from({ length: 9 }, (_, index) => ({
      ...baseAgent,
      id: `agent-${index + 1}`,
      name: `Agent ${index + 1}`,
    }))

    render(
      <RecentAgentsSection
        agents={agents}
        expanded={false}
        isSidebarCollapsed
        onExpandSidebar={onExpandSidebar}
        onToggleExpanded={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: '더 보기' })).toHaveClass(
      'group-data-[collapsible=icon]:hidden',
    )

    await user.click(screen.getByRole('link', { name: 'Agent 1' }))

    expect(onExpandSidebar).toHaveBeenCalledTimes(1)
  })

  it('hides recent sessions text pagination controls in the icon rail', () => {
    const conversations = Array.from({ length: 11 }, (_, index) => ({
      ...mockConversation,
      id: `conversation-${index + 1}`,
      title: `Conversation ${index + 1}`,
      agent: mockAgentSummaryList[0],
    }))

    render(
      <RecentSessionsSection
        actions={createRowActions()}
        activeConversationId={null}
        conversations={conversations}
        expanded={false}
        hasNextPage={false}
        isSidebarCollapsed
        isFetchingNextPage={false}
        isLoading={false}
        onExpandSidebar={vi.fn()}
        onMore={vi.fn()}
        search=""
      />,
    )

    expect(screen.getByRole('button', { name: '더 보기' })).toHaveClass(
      'group-data-[collapsible=icon]:hidden',
    )
  })
})
