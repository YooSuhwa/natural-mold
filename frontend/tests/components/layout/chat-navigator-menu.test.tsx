import { render, screen, userEvent } from '../../test-utils'
import { ChatNavigatorMenu } from '@/components/layout/chat-navigator-menu'

vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({
    children,
    render,
  }: {
    children: React.ReactNode
    render?: React.ReactElement
  }) => <div>{render ?? children}</div>,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuSub: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuSubTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuSubContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuRadioGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuRadioItem: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuCheckboxItem: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
}))

describe('ChatNavigatorMenu', () => {
  it('opens navigator controls with current labels', async () => {
    const user = userEvent.setup()
    render(
      <ChatNavigatorMenu
        mode="agent_grouped"
        agentSort="recent"
        sessionSort="updated"
        singleExpandedAgent={false}
        onModeChange={vi.fn()}
        onAgentSortChange={vi.fn()}
        onSessionSortChange={vi.fn()}
        onSingleExpandedAgentChange={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: '탐색 옵션' }))

    expect(screen.getByText('보기 방식')).toBeInTheDocument()
    expect(screen.getByText('에이전트 정렬')).toBeInTheDocument()
    expect(screen.getByText('대화 정렬')).toBeInTheDocument()
    expect(screen.getByText('한 번에 하나만 펼치기')).toBeInTheDocument()
  })
})
