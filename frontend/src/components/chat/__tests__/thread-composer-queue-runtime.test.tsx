import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type QueueItemState,
  type ThreadMessage,
} from '@assistant-ui/react'
import type { PropsWithChildren } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ThreadComposer } from '../assistant-thread-composer'
import { ServerMessageQueueProvider } from '@/lib/chat/message-queue/server-message-queue-context'
import type {
  ServerMessageQueueController,
  ServerMessageQueueSnapshot,
} from '@/lib/chat/message-queue/server-message-queue-contract'
import { render, screen, userEvent } from '../../../../tests/test-utils'

function RunningQueueRuntime({ children }: PropsWithChildren) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages: [],
    isRunning: true,
    onNew: async () => undefined,
    queue: {
      items: [],
      steerItems: [],
      enqueue: vi.fn(),
      steer: vi.fn(),
      move: vi.fn(),
      edit: vi.fn(),
      remove: vi.fn(),
    },
  })

  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
}

const promotedQueueItem: QueueItemState = {
  id: 'input-steer',
  prompt: 'promoted once',
  parts: [{ type: 'text', text: 'promoted once' }],
}

const promotedSnapshot: ServerMessageQueueSnapshot = {
  queuePaused: false,
  items: [
    {
      id: promotedQueueItem.id,
      conversation_id: 'conversation-1',
      run_id: null,
      client_request_id: 'request-steer',
      source: 'chat',
      status: 'pending',
      priority: 100,
      position: 1,
      revision: 2,
      input_payload: { messages: [{ role: 'user', content: promotedQueueItem.prompt }] },
      resource_context: [],
      attachment_ids: [],
      checkpoint_id: null,
      claimed_at: null,
      created_at: '2026-09-06T00:00:00Z',
      updated_at: '2026-09-06T00:00:00Z',
    },
  ],
  lastOperation: {
    kind: 'queued',
    requestId: 'request-steer',
    inputId: promotedQueueItem.id,
  },
  reconciliationError: null,
  rejectedSubmission: null,
}

const promotedController: ServerMessageQueueController = {
  adapter: {
    items: [],
    steerItems: [promotedQueueItem],
    enqueue: vi.fn(),
    steer: vi.fn(),
    move: vi.fn(),
    edit: vi.fn(),
    remove: vi.fn(),
  },
  enqueue: vi.fn(),
  steer: vi.fn(),
  edit: vi.fn(),
  move: vi.fn(),
  remove: vi.fn(),
  refresh: vi.fn(),
  reconcileRequest: vi.fn(),
  resume: vi.fn(),
  dismissRejectedSubmission: vi.fn(),
  getSnapshot: () => promotedSnapshot,
  subscribe: () => () => {},
}

function PromotedQueueRuntime({ children }: PropsWithChildren) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages: [],
    isRunning: true,
    onNew: async () => undefined,
    queue: promotedController.adapter,
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ServerMessageQueueProvider controller={promotedController}>
        {children}
      </ServerMessageQueueProvider>
    </AssistantRuntimeProvider>
  )
}

describe('ThreadComposer queue runtime controls', () => {
  it('renders actual Stop and Steer controls for a running public queue runtime', async () => {
    const user = userEvent.setup()
    render(
      <RunningQueueRuntime>
        <ThreadComposer />
      </RunningQueueRuntime>,
    )

    expect(screen.getByRole('button', { name: '중단' })).toBeVisible()
    const steer = screen.getByRole('button', {
      name: '현재 응답을 중단하고 바로 전송',
    })
    expect(steer).toBeVisible()
    expect(steer).toHaveAttribute('data-moldy-queue-steer-new')
    expect(steer).toBeDisabled()

    await user.type(screen.getByRole('textbox'), 'steer this message')

    expect(steer).toBeEnabled()
  })

  it('renders a promoted public queue item once without offering steer again', () => {
    render(
      <PromotedQueueRuntime>
        <ThreadComposer enableMessageQueue />
      </PromotedQueueRuntime>,
    )

    expect(document.querySelectorAll('[data-moldy-queue-item="input-steer"]')).toHaveLength(1)
    expect(
      screen.queryByRole('button', { name: '현재 응답을 중단하고 먼저 실행' }),
    ).not.toBeInTheDocument()
  })
})
