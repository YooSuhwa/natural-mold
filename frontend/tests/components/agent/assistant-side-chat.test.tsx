import { useState, type ReactNode } from 'react'
import { createStore, Provider } from 'jotai'

import {
  AssistantSideChatProvider,
  useAssistantSideChat,
} from '@/components/agent/assistant-side-chat-provider'
import type { AssistantPanelSession } from '@/components/agent/assistant-panel'
import type { Message } from '@/lib/types'
import { reconnectStateAtom, sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import { render, screen, userEvent, waitFor } from '../../test-utils'

const testState = vi.hoisted(() => ({
  isMobile: false,
  pathname: '/agents/agent-1/settings',
  userId: 'user-1' as string | null | undefined,
  sessionLoading: false,
  sessionFetched: true,
  replace: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => testState.pathname,
  useRouter: () => ({ replace: testState.replace }),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({
    data: typeof testState.userId === 'string' ? { id: testState.userId } : testState.userId,
    isLoading: testState.sessionLoading,
    isFetched: testState.sessionFetched,
  }),
}))

vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => testState.isMobile,
}))

vi.mock('@/components/agent/assistant-panel', async () => {
  const { useSetAtom } = await import('jotai')
  const { reconnectStateAtom, sessionTokenUsageAtom } = await import('@/lib/stores/chat-store')

  return {
    AssistantPanel: ({
      agentId,
      onClose,
      session,
    }: {
      agentId: string
      onClose?: () => void
      session: AssistantPanelSession
    }) => {
      const setReconnectState = useSetAtom(reconnectStateAtom)
      const setSessionTokenUsage = useSetAtom(sessionTokenUsageAtom)
      const simulateSideRuntimeEffect = () => {
        setReconnectState('reconnecting')
        setSessionTokenUsage({ inputTokens: 1, outputTokens: 2, cost: 3 })
      }

      return (
        <div
          data-testid="retained-assistant-panel"
          data-agent-id={agentId}
          data-message-count={session.messages.length}
          data-session-id={session.sessionId}
        >
          <button
            type="button"
            autoFocus
            onClick={() =>
              session.onMessagesCommit([
                {
                  id: `message-${session.messages.length + 1}`,
                  role: 'assistant',
                  content: 'side message',
                } as Message,
              ])
            }
          >
            commit side message
          </button>
          <button type="button" onClick={simulateSideRuntimeEffect}>
            simulate side success
          </button>
          <button type="button" onClick={simulateSideRuntimeEffect}>
            simulate side error
          </button>
          <button type="button" onClick={simulateSideRuntimeEffect}>
            simulate side cancel
          </button>
          <button type="button" onClick={onClose}>
            close side chat
          </button>
        </div>
      )
    },
  }
})

vi.mock('@/components/shared/dialog-shell', () => {
  function Root({ open, children }: { open: boolean; children: ReactNode }) {
    return open ? <div role="dialog">{children}</div> : null
  }
  const Header = ({ title }: { title: ReactNode }) => <header>{title}</header>
  const Body = ({ children }: { children: ReactNode }) => <div>{children}</div>
  return { DialogShell: Object.assign(Root, { Header, Body }) }
})

function SideChatControls() {
  const sideChat = useAssistantSideChat()
  const [mainMessages] = useState(['main message'])
  const [pageDialogOpen, setPageDialogOpen] = useState(false)
  const [pageDraft, setPageDraft] = useState('')
  return (
    <>
      <p data-testid="main-transcript">{mainMessages.join(',')}</p>
      <button type="button" onClick={() => setPageDialogOpen(true)}>
        open page dialog
      </button>
      {pageDialogOpen ? (
        <div role="dialog" aria-label="page dialog">
          <input
            aria-label="page draft"
            value={pageDraft}
            onChange={(event) => setPageDraft(event.target.value)}
          />
        </div>
      ) : null}
      <button
        type="button"
        onClick={(event) =>
          sideChat.openForAgent({ agentId: 'agent-1', agentName: 'Agent One' }, event.currentTarget)
        }
      >
        open agent one
      </button>
      <button
        type="button"
        onClick={() => sideChat.openForAgent({ agentId: 'agent-2', agentName: 'Agent Two' })}
      >
        open agent two
      </button>
    </>
  )
}

function renderSideChat(mainStore = createStore()) {
  return render(
    <Provider store={mainStore}>
      <AssistantSideChatProvider>
        <SideChatControls />
      </AssistantSideChatProvider>
    </Provider>,
  )
}

describe('AssistantSideChatProvider', () => {
  beforeEach(() => {
    testState.isMobile = false
    testState.pathname = '/agents/agent-1/settings'
    testState.userId = 'user-1'
    testState.sessionLoading = false
    testState.sessionFetched = true
    testState.replace.mockReset()
    window.requestAnimationFrame = (callback) => {
      callback(0)
      return 1
    }
  })

  it.each([true, false])(
    '늦은 session 응답 전에는 page를 노출하지 않고 열린 dialog를 유지한다 (loading=%s)',
    async (sessionLoading) => {
      testState.userId = undefined
      testState.sessionLoading = sessionLoading
      testState.sessionFetched = false
      const user = userEvent.setup()
      const mainStore = createStore()
      const view = renderSideChat(mainStore)

      expect(screen.queryByRole('button', { name: 'open page dialog' })).not.toBeInTheDocument()
      expect(screen.queryByTestId('main-transcript')).not.toBeInTheDocument()

      testState.userId = 'user-1'
      testState.sessionLoading = false
      testState.sessionFetched = true
      const tree = () => (
        <Provider store={mainStore}>
          <AssistantSideChatProvider>
            <SideChatControls />
          </AssistantSideChatProvider>
        </Provider>
      )
      view.rerender(tree())
      await user.click(screen.getByRole('button', { name: 'open page dialog' }))
      await user.type(screen.getByRole('textbox', { name: 'page draft' }), 'keep this draft')

      // A same-account session refresh must not replace already interactive page children.
      view.rerender(tree())
      expect(screen.getByRole('dialog', { name: 'page dialog' })).toBeInTheDocument()
      expect(screen.getByRole('textbox', { name: 'page draft' })).toHaveValue('keep this draft')
    },
  )

  it('로그아웃하면 page와 보관한 사이드 세션을 제거하고 로그인으로 이동한다', async () => {
    const user = userEvent.setup()
    const mainStore = createStore()
    const view = renderSideChat(mainStore)
    const tree = () => (
      <Provider store={mainStore}>
        <AssistantSideChatProvider>
          <SideChatControls />
        </AssistantSideChatProvider>
      </Provider>
    )
    await user.click(screen.getByRole('button', { name: 'open agent one' }))
    const firstSessionId = screen
      .getByTestId('retained-assistant-panel')
      .getAttribute('data-session-id')
    await user.click(screen.getByRole('button', { name: 'commit side message' }))

    testState.userId = null
    view.rerender(tree())
    expect(screen.queryByTestId('main-transcript')).not.toBeInTheDocument()
    expect(screen.queryByTestId('retained-assistant-panel')).not.toBeInTheDocument()
    expect(testState.replace).toHaveBeenCalledWith('/login?callbackUrl=%2Fagents%2Fagent-1%2Fsettings')

    testState.userId = 'user-1'
    view.rerender(tree())
    await user.click(screen.getByRole('button', { name: 'open agent one' }))
    expect(screen.getByTestId('retained-assistant-panel')).toHaveAttribute('data-message-count', '0')
    expect(screen.getByTestId('retained-assistant-panel')).not.toHaveAttribute(
      'data-session-id',
      firstSessionId,
    )
  })

  it('닫았다 다시 연 같은 에이전트의 독립 메시지와 세션을 유지한다', async () => {
    const user = userEvent.setup()
    renderSideChat()

    await user.click(screen.getByRole('button', { name: 'open agent one' }))
    const initialPanel = screen.getByTestId('retained-assistant-panel')
    const initialSessionId = initialPanel.getAttribute('data-session-id')
    await user.click(screen.getByRole('button', { name: 'commit side message' }))
    expect(screen.getByTestId('retained-assistant-panel')).toHaveAttribute(
      'data-message-count',
      '1',
    )

    await user.click(screen.getByRole('button', { name: 'close side chat' }))
    expect(screen.queryByTestId('assistant-side-chat')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'open agent one' }))

    expect(screen.getByTestId('retained-assistant-panel')).toHaveAttribute(
      'data-session-id',
      initialSessionId,
    )
    expect(screen.getByTestId('retained-assistant-panel')).toHaveAttribute(
      'data-message-count',
      '1',
    )
  })

  it('에이전트별 사이드 transcript를 분리한다', async () => {
    const user = userEvent.setup()
    renderSideChat()

    await user.click(screen.getByRole('button', { name: 'open agent one' }))
    await user.click(screen.getByRole('button', { name: 'commit side message' }))
    await user.click(screen.getByRole('button', { name: 'open agent two' }))
    expect(screen.getByTestId('retained-assistant-panel')).toHaveAttribute(
      'data-agent-id',
      'agent-2',
    )
    expect(screen.getByTestId('retained-assistant-panel')).toHaveAttribute(
      'data-message-count',
      '0',
    )

    await user.click(screen.getByRole('button', { name: 'open agent one' }))
    expect(screen.getByTestId('retained-assistant-panel')).toHaveAttribute(
      'data-message-count',
      '1',
    )
  })

  it('사이드 메시지 변경과 닫기가 메인 transcript를 변경하지 않는다', async () => {
    const user = userEvent.setup()
    renderSideChat()

    await user.click(screen.getByRole('button', { name: 'open agent one' }))
    await user.click(screen.getByRole('button', { name: 'commit side message' }))
    await user.click(screen.getByRole('button', { name: 'close side chat' }))

    expect(screen.getByTestId('main-transcript')).toHaveTextContent('main message')
  })

  it('사이드 runtime 성공·오류·취소와 닫기가 메인 표시 atom을 변경하지 않는다', async () => {
    const user = userEvent.setup()
    const mainStore = createStore()
    const mainUsage = { inputTokens: 101, outputTokens: 202, cost: 3.03 }
    mainStore.set(sessionTokenUsageAtom, mainUsage)
    mainStore.set(reconnectStateAtom, 'reconnecting')
    renderSideChat(mainStore)

    await user.click(screen.getByRole('button', { name: 'open agent one' }))
    for (const action of ['simulate side success', 'simulate side error', 'simulate side cancel']) {
      await user.click(screen.getByRole('button', { name: action }))
      expect(mainStore.get(sessionTokenUsageAtom)).toEqual(mainUsage)
      expect(mainStore.get(reconnectStateAtom)).toBe('reconnecting')
    }
    await user.click(screen.getByRole('button', { name: 'close side chat' }))

    expect(mainStore.get(sessionTokenUsageAtom)).toEqual(mainUsage)
    expect(mainStore.get(reconnectStateAtom)).toBe('reconnecting')
  })

  it('다른 에이전트 경로로 이동하면 열린 패널을 닫는다', async () => {
    const user = userEvent.setup()
    const mainStore = createStore()
    const view = renderSideChat(mainStore)

    await user.click(screen.getByRole('button', { name: 'open agent one' }))
    expect(screen.getByTestId('assistant-side-chat')).toBeInTheDocument()
    testState.pathname = '/agents/agent-2/settings'
    view.rerender(
      <Provider store={mainStore}>
        <AssistantSideChatProvider>
          <SideChatControls />
        </AssistantSideChatProvider>
      </Provider>,
    )

    await waitFor(() => expect(screen.queryByTestId('assistant-side-chat')).not.toBeInTheDocument())
  })

  it('사용자가 바뀌면 보관한 사이드 세션을 비운다', async () => {
    const user = userEvent.setup()
    const mainStore = createStore()
    const view = renderSideChat(mainStore)

    await user.click(screen.getByRole('button', { name: 'open page dialog' }))
    await user.type(screen.getByRole('textbox', { name: 'page draft' }), 'private user-one draft')
    await user.click(screen.getByRole('button', { name: 'open agent one' }))
    const firstSessionId = screen
      .getByTestId('retained-assistant-panel')
      .getAttribute('data-session-id')
    await user.click(screen.getByRole('button', { name: 'commit side message' }))

    testState.userId = 'user-2'
    view.rerender(
      <Provider store={mainStore}>
        <AssistantSideChatProvider>
          <SideChatControls />
        </AssistantSideChatProvider>
      </Provider>,
    )
    expect(screen.queryByRole('dialog', { name: 'page dialog' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'open agent one' }))

    expect(screen.getByTestId('retained-assistant-panel')).toHaveAttribute(
      'data-message-count',
      '0',
    )
    expect(screen.getByTestId('retained-assistant-panel')).not.toHaveAttribute(
      'data-session-id',
      firstSessionId,
    )
  })

  it('모바일 dialog에서 composer에 초점을 두고 닫으면 trigger로 초점을 돌린다', async () => {
    testState.isMobile = true
    const user = userEvent.setup()
    renderSideChat()
    const trigger = screen.getByRole('button', { name: 'open agent one' })

    await user.click(trigger)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'commit side message' })).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'close side chat' }))

    expect(trigger).toHaveFocus()
  })
})
