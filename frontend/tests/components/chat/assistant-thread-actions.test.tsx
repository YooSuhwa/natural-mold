import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../test-utils'

vi.mock('@/components/agent/agent-avatar', () => ({
  AgentAvatar: () => <div data-testid="agent-avatar" />,
}))

vi.mock('@/components/auth/UserAvatar', () => ({
  UserAvatar: ({ user }: { user?: { display_name?: string | null } | null }) => (
    <div data-testid="user-avatar">{user?.display_name}</div>
  ),
}))

vi.mock('@assistant-ui/react', () => {
  const passthrough = ({ children, className }: { children?: ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  )
  const childPassthrough = ({ children }: { children?: ReactNode }) => <>{children}</>
  const actionButton = ({
    children,
    className,
    ...props
  }: {
    children?: ReactNode
    className?: string
    'aria-label'?: string
    copiedDuration?: number
    onClick?: () => void
  }) => (
    <button type="button" className={className} aria-label={props['aria-label']}>
      {children}
    </button>
  )

  return {
    ThreadPrimitive: {
      Root: passthrough,
      Viewport: passthrough,
      Empty: () => null,
      Messages: ({
        children,
      }: {
        children: (props: {
          message: { role: string; composer: { isEditing: boolean } }
        }) => ReactNode
      }) => (
        <>
          {children({ message: { role: 'user', composer: { isEditing: false } } })}
          {children({ message: { role: 'assistant', composer: { isEditing: false } } })}
        </>
      ),
      ViewportFooter: passthrough,
      ScrollToBottom: childPassthrough,
      If: ({ running, children }: { running: boolean; children?: ReactNode }) =>
        running ? null : <>{children}</>,
    },
    MessagePrimitive: {
      Content: () => <span>메시지</span>,
    },
    ComposerPrimitive: {
      Root: passthrough,
      Input: () => <textarea aria-label="message input" />,
      Cancel: childPassthrough,
      Send: childPassthrough,
      Attachments: () => null,
      AddAttachment: childPassthrough,
    },
    AttachmentPrimitive: {
      Root: passthrough,
      Name: () => <span>file.txt</span>,
      Remove: childPassthrough,
    },
    ActionBarPrimitive: {
      Copy: actionButton,
      Edit: actionButton,
      Reload: actionButton,
      FeedbackPositive: actionButton,
      FeedbackNegative: actionButton,
    },
    useThreadViewport: (selector: (state: { isAtBottom: boolean }) => unknown) =>
      selector({ isAtBottom: true }),
    useAssistantState: (selector: (state: unknown) => unknown) =>
      selector({
        message: {
          status: { type: 'complete' },
          metadata: { custom: {}, submittedFeedback: undefined },
        },
      }),
    useAuiState: (selector: (state: unknown) => unknown) =>
      selector({
        composer: { dictation: null, isEditing: true, text: '' },
        thread: { isDisabled: false },
      }),
    useAui: () => ({
      composer: () => ({
        addAttachment: vi.fn(),
        getState: () => ({ isEditing: true, isEmpty: true }),
        send: vi.fn(),
        setText: vi.fn(),
      }),
      thread: () => ({
        cancelRun: vi.fn(),
        getState: () => ({ capabilities: { attachments: false, queue: false }, isRunning: false }),
      }),
    }),
    AuiIf: ({
      condition,
      children,
    }: {
      condition: (s: { thread: { isRunning: boolean; isEmpty: boolean } }) => boolean
      children?: ReactNode
    }) => (condition({ thread: { isRunning: false, isEmpty: false } }) ? <>{children}</> : null),
    getExternalStoreMessages: () => [],
    makeAssistantToolUI: () => () => <div data-testid="tool-ui" />,
  }
})

import { AssistantThread } from '@/components/chat/assistant-thread'

describe('AssistantThread message actions', () => {
  it('renders the session user avatar for default user messages', () => {
    render(
      <AssistantThread
        user={{
          id: 'user-1',
          name: 'Real Name',
          display_name: '체스터',
          email: 'chester@example.com',
          is_super_user: false,
          created_at: '2026-05-01T00:00:00Z',
        }}
      />,
    )

    expect(screen.getAllByTestId('user-avatar')[0]).toHaveTextContent('체스터')
  })

  it('keeps builder user messages avatar-free', () => {
    render(
      <AssistantThread
        variant="builder"
        user={{
          id: 'user-1',
          name: 'Real Name',
          display_name: '체스터',
          email: 'chester@example.com',
          is_super_user: false,
          created_at: '2026-05-01T00:00:00Z',
        }}
      />,
    )

    expect(screen.queryByTestId('user-avatar')).not.toBeInTheDocument()
  })

  it('renders message actions as fixed-width icon-first controls with accessible names', () => {
    render(<AssistantThread />)

    const actionButtons = [
      ...screen.getAllByRole('button', { name: '복사' }),
      screen.getByRole('button', { name: '편집' }),
      screen.getByRole('button', { name: '재생성' }),
      screen.getByRole('button', { name: '도움이 됨' }),
      screen.getByRole('button', { name: '도움이 안 됨' }),
    ]

    for (const button of actionButtons) {
      expect(button).toHaveClass('size-7')
      expect(button).toHaveClass('shrink-0')
      expect(button).toHaveClass('justify-center')
    }
  })
})
