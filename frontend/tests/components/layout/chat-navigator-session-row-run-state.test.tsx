import { render, screen } from '../../test-utils'
import { Provider, createStore } from 'jotai'
import { ChatNavigatorSessionRow } from '@/components/layout/chat-navigator-session-row'
import type { ConversationRowActions } from '@/components/chat/use-conversation-row-actions'
import { conversationRuntimeStatusAtom } from '@/lib/stores/chat-navigator-store'
import type { Conversation, ConversationRun } from '@/lib/types'
import { mockConversation } from '../../mocks/fixtures'

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

function conversation(
  id: string,
  title: string,
  status: ConversationRun['status'] | null,
): Conversation {
  return {
    ...mockConversation,
    id,
    title,
    active_run: status ? run(id, status) : null,
  }
}

describe('ChatNavigatorSessionRow run 상태 표시', () => {
  it('active run에만 스피너를, interrupted run에는 확인 필요 마커를 표시한다', () => {
    render(
      <>
        <ChatNavigatorSessionRow
          conversation={conversation('conversation-running', 'Running session', 'running')}
          active={false}
          actions={createRowActions()}
        />
        <ChatNavigatorSessionRow
          conversation={conversation('conversation-interrupted', 'Needs action', 'interrupted')}
          active={false}
          actions={createRowActions()}
        />
        <ChatNavigatorSessionRow
          conversation={conversation('conversation-completed', 'Done session', 'completed')}
          active={false}
          actions={createRowActions()}
        />
      </>,
    )

    expect(screen.getByText('Running session')).toBeInTheDocument()
    expect(screen.getByLabelText('답변 생성 중')).toHaveAttribute(
      'data-moldy-run-spinner',
      'conversation-running',
    )

    expect(screen.getByText('Needs action')).toBeInTheDocument()
    expect(screen.getByLabelText('사용자 확인 필요')).toHaveAttribute(
      'data-moldy-run-attention',
      'conversation-interrupted',
    )

    expect(screen.getByText('Done session')).toBeInTheDocument()
    expect(screen.queryAllByLabelText('답변 생성 중')).toHaveLength(1)
    expect(screen.queryAllByLabelText('사용자 확인 필요')).toHaveLength(1)
  })

  it('active_run이 없어도 같은 탭 스트리밍 오버레이(atom)가 running이면 스피너를 표시한다', () => {
    const store = createStore()
    store.set(conversationRuntimeStatusAtom, { 'conversation-local': 'running' })

    render(
      <Provider store={store}>
        <ChatNavigatorSessionRow
          conversation={conversation('conversation-local', 'Local stream', null)}
          active={false}
          actions={createRowActions()}
        />
      </Provider>,
    )

    expect(screen.getByLabelText('답변 생성 중')).toHaveAttribute(
      'data-moldy-run-spinner',
      'conversation-local',
    )
  })
})
