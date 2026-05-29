import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../test-utils'

vi.mock('@/components/agent/agent-avatar', () => ({
  AgentAvatar: () => <div data-testid="agent-avatar" />,
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
        components,
      }: {
        components: {
          UserMessage: () => ReactNode
          AssistantMessage: () => ReactNode
        }
      }) => (
        <>
          {components.UserMessage()}
          {components.AssistantMessage()}
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
    useAui: () => ({
      thread: () => ({ cancelRun: vi.fn() }),
    }),
    makeAssistantToolUI: (config: unknown) => () => <div data-testid="tool-ui" />,
  }
})

import { AssistantThread } from '@/components/chat/assistant-thread'

describe('AssistantThread message actions', () => {
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
