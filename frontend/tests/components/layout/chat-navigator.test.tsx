import { render, screen, waitFor, userEvent } from '../../test-utils'
import { act, fireEvent } from '@testing-library/react'
import { Provider, createStore } from 'jotai'
import { ChatNavigator } from '@/components/layout/chat-navigator'
import {
  ChatNavigatorAgentGroup,
  agentSessionScope,
} from '@/components/layout/chat-navigator-agent-group'
import { ChatNavigatorSessionRow } from '@/components/layout/chat-navigator-session-row'
import { formatShortcutLabel } from '@/components/layout/use-chat-navigator-shortcuts'
import type { ConversationRowActions } from '@/components/chat/use-conversation-row-actions'
import { shortcutPreviewActiveAtom } from '@/lib/stores/chat-navigator-store'
import {
  mockAgentSummaryList,
  mockConversation,
  mockConversationPage,
  mockGlobalConversationPage,
} from '../../mocks/fixtures'

const conversationHookMocks = vi.hoisted(() => ({
  invalidateConversationNavigators: vi.fn(),
  useConversationPages: vi.fn(),
  useGlobalConversationPages: vi.fn(),
}))

const routerMocks = vi.hoisted(() => ({
  push: vi.fn(),
}))

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

vi.mock('next/navigation', () => ({
  usePathname: () => '/agents/agent-1/conversations/new',
  useRouter: () => ({ push: routerMocks.push }),
}))

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgentSummaries: () => ({ data: mockAgentSummaryList, isLoading: false }),
}))

vi.mock('@/lib/hooks/use-conversations', () => ({
  invalidateConversationNavigators: conversationHookMocks.invalidateConversationNavigators,
  useConversationPages: conversationHookMocks.useConversationPages,
  useGlobalConversationPages: conversationHookMocks.useGlobalConversationPages,
}))

vi.mock('@/components/chat/use-conversation-row-actions', () => ({
  useConversationRowActions: () => ({
    isDeleting: false,
    dialogs: null,
    openRenameDialog: vi.fn(),
    openShareDialog: vi.fn(),
    requestDelete: vi.fn(),
    togglePin: vi.fn(),
  }),
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

describe('ChatNavigator', () => {
  beforeEach(() => {
    routerMocks.push.mockClear()
    // atomWithStorage(collapsedAgentIdsAtom)가 테스트 간 상태를 누수하지 않게 한다
    window.localStorage.clear()
    conversationHookMocks.useConversationPages.mockReturnValue({
      data: { pages: [mockConversationPage] },
      isLoading: false,
      hasNextPage: false,
      fetchNextPage: vi.fn(),
      isFetchingNextPage: false,
    })
    conversationHookMocks.useGlobalConversationPages.mockReturnValue({
      data: { pages: [mockGlobalConversationPage] },
      isLoading: false,
      hasNextPage: false,
      fetchNextPage: vi.fn(),
      isFetchingNextPage: false,
    })
  })

  it('renders agent-grouped navigation and the local draft row', async () => {
    render(<ChatNavigator />)

    expect(screen.getByText('에이전트')).toBeInTheDocument()
    expect(screen.getByText('Test Agent')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('link', { name: /새 대화/ })).toBeInTheDocument())
    expect(screen.getByText('Test Conversation')).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: '에이전트 또는 대화 검색' })).not.toBeInTheDocument()

    const activeAgentNewChat = screen.getByRole('button', { name: 'Test Agent 새 채팅' })
    const activeAgentControls = activeAgentNewChat.closest('div')
    if (!activeAgentControls) {
      throw new TypeError('active agent controls container was missing')
    }
    expect(activeAgentControls).toHaveClass('opacity-100')
    expect(screen.queryByRole('button', { name: 'Test Agent 대화 검색' })).not.toBeInTheDocument()
  })

  it('shows global search results above agent groups', async () => {
    const user = userEvent.setup()
    render(<ChatNavigator />)

    await user.click(screen.getByRole('button', { name: '에이전트 검색' }))
    await user.type(screen.getByRole('textbox', { name: '에이전트 또는 대화 검색' }), 'Second')

    expect(screen.getByText('검색 결과')).toBeInTheDocument()
    expect(screen.getAllByText('Second Conversation').length).toBeGreaterThan(0)
    expect(screen.queryByText('검색 결과가 없습니다')).not.toBeInTheDocument()
  })

  it('expands collapsed agent sessions before fetching another page', async () => {
    const user = userEvent.setup()
    const fetchNextPage = vi.fn()
    const onToggleListExpanded = vi.fn()
    const agent = mockAgentSummaryList[0]
    if (!agent) {
      throw new TypeError('agent fixture was missing')
    }
    const conversations = Array.from({ length: 6 }, (_, index) => ({
      ...mockConversation,
      id: `conv-${index + 1}`,
      title: `Session ${index + 1}`,
    }))
    conversationHookMocks.useConversationPages.mockReturnValue({
      data: { pages: [{ ...mockConversationPage, items: conversations }] },
      isLoading: false,
      hasNextPage: true,
      fetchNextPage,
      isFetchingNextPage: false,
    })

    render(
      <ChatNavigatorAgentGroup
        agent={agent}
        activeAgentId="agent-1"
        activeConversationId="conv-1"
        searchQuery=""
        sessionSort="updated"
        expanded
        listExpanded={false}
        shortcutHintsEnabled
        onToggleExpanded={vi.fn()}
        onToggleListExpanded={onToggleListExpanded}
        actions={createRowActions()}
      />,
    )

    await user.click(screen.getByRole('button', { name: '더 보기' }))

    expect(onToggleListExpanded).toHaveBeenCalledWith(agentSessionScope('agent-1'))
    expect(fetchNextPage).not.toHaveBeenCalled()
  })

  it('navigates with Cmd+Shift+digit even when event.key is a layout character', () => {
    render(<ChatNavigator />)

    const firstHref = document
      .querySelector('[data-chat-session-href]')
      ?.getAttribute('data-chat-session-href')
    expect(firstHref).toBeTruthy()

    // IME 조합 중에는 단축키가 동작하지 않아야 한다
    fireEvent.keyDown(window, {
      key: '!',
      code: 'Digit1',
      metaKey: true,
      shiftKey: true,
      isComposing: true,
    })
    expect(routerMocks.push).not.toHaveBeenCalled()

    // macOS에서 Cmd+Shift+1은 event.key가 '!'로 들어온다 — 물리 키 코드로 매칭해야 한다
    fireEvent.keyDown(window, { key: '!', code: 'Digit1', metaKey: true, shiftKey: true })

    expect(routerMocks.push).toHaveBeenCalledWith(firstHref)
  })

  it('does not navigate with Cmd+Shift+digit while an editable element is focused', () => {
    render(<ChatNavigator />)

    const textarea = document.createElement('textarea')
    document.body.appendChild(textarea)
    textarea.focus()

    // 입력 요소 포커스 중 내비게이션은 작성 중인 draft를 유실시키므로 무시해야 한다
    fireEvent.keyDown(textarea, { key: '!', code: 'Digit1', metaKey: true, shiftKey: true })

    expect(routerMocks.push).not.toHaveBeenCalled()
    textarea.remove()
  })

  it('collapses and re-expands the active agent group via the toggle', async () => {
    const user = userEvent.setup()
    render(
      <Provider store={createStore()}>
        <ChatNavigator />
      </Provider>,
    )

    // 활성 에이전트(agent-1)는 collapse override가 없는 한 기본 펼침이다
    expect(screen.getByText('Test Conversation')).toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: '에이전트 접기' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Test Conversation')).not.toBeInTheDocument()

    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Test Conversation')).toBeInTheDocument()
  })

  it('renders shortcut hints from the global session row order', async () => {
    const store = createStore()
    const actions = createRowActions()

    render(
      <Provider store={store}>
        <ChatNavigatorSessionRow
          conversation={{ ...mockConversation, id: 'conv-a', title: 'First session' }}
          active={false}
          shortcutIndex={1}
          actions={actions}
        />
        <ChatNavigatorSessionRow
          conversation={{ ...mockConversation, id: 'conv-b', title: 'Second session' }}
          active={false}
          shortcutIndex={2}
          actions={actions}
        />
        <ChatNavigatorSessionRow
          conversation={{ ...mockConversation, id: 'conv-c', title: 'Tenth session' }}
          active={false}
          shortcutIndex={10}
          actions={actions}
        />
      </Provider>,
    )
    act(() => store.set(shortcutPreviewActiveAtom, true))

    await waitFor(() => expect(screen.getByText(formatShortcutLabel(1))).toBeInTheDocument())
    expect(screen.getByText(formatShortcutLabel(2))).toBeInTheDocument()
    // 단축키는 Digit1~9까지만 매핑되므로 10번째 이후 행에는 힌트를 그리지 않는다
    expect(screen.queryByText(formatShortcutLabel(10))).not.toBeInTheDocument()
  })
})
