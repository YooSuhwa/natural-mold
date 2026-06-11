import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ConversationList } from '../conversation-list'
import type { Conversation, ConversationRun } from '@/lib/types'

vi.mock('next/link', () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string
    children: React.ReactNode
    className?: string
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}))

vi.mock('next/navigation', () => ({
  useParams: () => ({ conversationId: 'conversation-running' }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('next-intl', () => ({
  useTranslations: (namespace: string) => (key: string) => `${namespace}.${key}`,
}))

vi.mock('@/lib/hooks/use-conversations', () => ({
  useConversationPages: () => ({
    data: {
      pages: [
        {
          items: [
            conversation('conversation-running', 'Running session', 'running'),
            conversation('conversation-interrupted', 'Needs action', 'interrupted'),
            conversation('conversation-completed', 'Done session', 'completed'),
          ],
          next_cursor: null,
          has_more: false,
        },
      ],
    },
    isLoading: false,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
    isFetchingNextPage: false,
  }),
  useUpdateConversation: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteConversation: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock('@/components/shared/delete-confirm-dialog', () => ({
  DeleteConfirmDialog: () => null,
}))

vi.mock('@/components/shared/dialog-shell', () => ({
  DialogShell: Object.assign(({ children }: { children: React.ReactNode }) => <>{children}</>, {
    Header: () => null,
    Body: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Footer: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  }),
}))

vi.mock('@/components/chat/share-dialog', () => ({
  ShareDialog: () => null,
}))

vi.mock('@/components/shared/search-input', () => ({
  SearchInput: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}))

vi.mock('@/components/agent/agent-avatar', () => ({
  AgentAvatar: () => <div data-testid="agent-avatar" />,
}))

vi.mock('@/lib/utils/format-relative-time', () => ({
  formatRelativeShort: () => 'now',
}))

function run(conversationId: string, status: ConversationRun['status']): ConversationRun {
  return {
    id: `run-${status}`,
    conversation_id: conversationId,
    agent_id: 'agent-1',
    parent_run_id: null,
    status,
    source: 'chat',
    worker_instance_id: null,
    interrupt_id: null,
    last_event_id: null,
    input_preview: null,
    error_code: null,
    error_message: null,
    cancel_requested_at: null,
    started_at: null,
    heartbeat_at: null,
    completed_at: null,
    created_at: '2026-06-11T00:00:00.000Z',
    updated_at: '2026-06-11T00:00:00.000Z',
  }
}

function conversation(id: string, title: string, status: ConversationRun['status']): Conversation {
  return {
    id,
    agent_id: 'agent-1',
    title,
    is_pinned: false,
    unread_count: 0,
    last_read_at: null,
    last_unread_at: null,
    last_activity_source: 'message',
    created_at: '2026-06-11T00:00:00.000Z',
    updated_at: '2026-06-11T00:00:00.000Z',
    active_run: run(id, status),
  }
}

describe('ConversationList run state', () => {
  it('shows a spinner only for active runs and an attention marker for interrupted runs', () => {
    render(<ConversationList agentId="agent-1" agentName="Agent" />)

    expect(screen.getByText('Running session')).toBeInTheDocument()
    expect(screen.getByLabelText('chat.conversationList.status.running')).toHaveAttribute(
      'data-moldy-run-spinner',
      'conversation-running',
    )

    expect(screen.getByText('Needs action')).toBeInTheDocument()
    expect(screen.getByLabelText('chat.conversationList.status.actionRequired')).toHaveAttribute(
      'data-moldy-run-attention',
      'conversation-interrupted',
    )

    expect(screen.getByText('Done session')).toBeInTheDocument()
    expect(screen.queryByTestId('conversation-completed')).not.toBeInTheDocument()
    expect(screen.queryAllByLabelText('chat.conversationList.status.running')).toHaveLength(1)
  })
})
