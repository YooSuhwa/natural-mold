import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render } from '../../test-utils'

vi.mock('@/components/agent/agent-avatar', () => ({
  AgentAvatar: () => <div data-testid="agent-avatar" />,
}))

vi.mock('@assistant-ui/react', () => {
  const passthrough = ({ children, className }: { children?: ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  )
  const childPassthrough = ({ children }: { children?: ReactNode }) => <>{children}</>

  return {
    ThreadPrimitive: {
      Root: passthrough,
      Viewport: passthrough,
      Empty: () => null,
      Messages: ({
        components,
      }: {
        components: {
          UserEditComposer: () => ReactNode
        }
      }) => <>{components.UserEditComposer()}</>,
      ViewportFooter: () => null,
      ScrollToBottom: childPassthrough,
      If: ({ children }: { children?: ReactNode }) => <>{children}</>,
    },
    MessagePrimitive: {
      Content: () => <span>메시지</span>,
    },
    ComposerPrimitive: {
      Root: ({ children, className }: { children?: ReactNode; className?: string }) => {
        if (className?.includes('flex flex-col gap-2')) {
          throw new Error('Composer is not available')
        }
        return <form className={className}>{children}</form>
      },
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
      Copy: childPassthrough,
      Edit: childPassthrough,
      Reload: childPassthrough,
      FeedbackPositive: childPassthrough,
      FeedbackNegative: childPassthrough,
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
        message: {
          composer: {
            canCancel: true,
            dictation: null,
            isEditing: true,
            isEmpty: false,
            text: '초안',
          },
        },
        thread: { isDisabled: false, isRunning: false },
      }),
    useAui: () => ({
      message: () => ({
        composer: () => ({
          cancel: vi.fn(),
          getState: () => ({ canCancel: true, isEditing: true, isEmpty: false }),
          send: vi.fn(),
          setText: vi.fn(),
        }),
      }),
      thread: () => ({
        getState: () => ({ capabilities: { attachments: false, queue: false }, isRunning: false }),
      }),
    }),
    makeAssistantToolUI: () => () => <div data-testid="tool-ui" />,
    useMessagePartText: () => ({ text: '' }),
  }
})

import { AssistantThread } from '@/components/chat/assistant-thread'

describe('AssistantThread user edit composer', () => {
  it('renders the default edit composer without requiring the thread composer context', () => {
    expect(() => render(<AssistantThread />)).not.toThrow('Composer is not available')
  })

  it('renders the builder edit composer without requiring the thread composer context', () => {
    expect(() => render(<AssistantThread variant="builder" />)).not.toThrow(
      'Composer is not available',
    )
  })
})
