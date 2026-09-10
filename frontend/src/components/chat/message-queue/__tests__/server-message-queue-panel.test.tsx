import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  addAttachment: vi.fn(),
  getComposerState: vi.fn(() => ({ text: '', runConfig: {} })),
  setRunConfig: vi.fn(),
  setText: vi.fn(),
  queueItems: [
    {
      id: 'input-1',
      prompt: 'queued one',
      parts: [{ type: 'text' as const, text: 'queued one' }],
    },
    {
      id: 'input-2',
      prompt: 'queued two',
      parts: [{ type: 'text' as const, text: 'queued two' }],
    },
  ],
  officialSteer: vi.fn(),
}))

vi.mock('@assistant-ui/react', () => ({
  useAui: () => ({
    composer: {
      addAttachment: mocks.addAttachment,
      getState: mocks.getComposerState,
      setRunConfig: mocks.setRunConfig,
      setText: mocks.setText,
    },
  }),
  ComposerPrimitive: {
    Queue: ({
      children,
    }: {
      children: (value: { queueItem: (typeof mocks.queueItems)[number] }) => ReactNode
    }) =>
      mocks.queueItems.map((queueItem) => <div key={queueItem.id}>{children({ queueItem })}</div>),
  },
  QueueItemPrimitive: {
    Text: () => <span>queued message</span>,
    Steer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ onClick?: () => void }>, {
            onClick: mocks.officialSteer,
          })
        : children,
    Remove: ({ children }: { children: ReactNode }) => children,
  },
}))

import { ServerMessageQueueProvider } from '@/lib/chat/message-queue/server-message-queue-context'
import type { ServerMessageQueueController } from '@/lib/chat/message-queue/server-message-queue-contract'
import { render, screen, userEvent } from '../../../../../tests/test-utils'
import { ServerMessageQueuePanel, type MessageQueueLabels } from '../server-message-queue-panel'

const labels: MessageQueueLabels = {
  title: 'Queue',
  paused: 'Paused',
  resume: 'Resume',
  edit: 'Edit',
  save: 'Save',
  cancelEdit: 'Cancel edit',
  remove: 'Remove',
  steer: 'Steer',
  steerAction: 'Steer',
  sendNow: 'Send now',
  cancelSteer: 'Cancel steer',
  moreActions: 'More actions',
  moveUp: 'Move up',
  moveDown: 'Move down',
  sending: 'Sending',
  queued: 'Queued',
  applied: 'Applied',
  failed: 'Failed',
  restore: 'Restore draft',
}

function controller(): ServerMessageQueueController {
  const items = mocks.queueItems.map((item, index) => ({
    id: item.id,
    conversation_id: 'conversation-1',
    run_id: null,
    client_request_id: `request-${index + 1}`,
    source: 'chat',
    status: 'pending' as const,
    priority: 0,
    position: index + 1,
    revision: 1,
    input_payload: { messages: [{ role: 'user', content: item.prompt }] },
    resource_context: [],
    attachment_ids: [],
    checkpoint_id: null,
    claimed_at: null,
    created_at: '2026-09-06T00:00:00',
    updated_at: '2026-09-06T00:00:00',
  }))
  const snapshot = {
    queuePaused: true,
    items,
    lastOperation: { kind: 'queued' as const, requestId: 'request-1', inputId: 'input-1' },
    reconciliationError: null,
    rejectedSubmission: null,
  }
  return {
    adapter: {
      items: mocks.queueItems,
      steerItems: [],
      enqueue: vi.fn(),
      steer: vi.fn(),
      move: vi.fn(),
      edit: vi.fn(),
      remove: vi.fn(),
    },
    updateCallbacks: vi.fn(),
    enqueue: vi.fn(),
    steer: vi.fn(),
    edit: vi.fn(),
    move: vi.fn(),
    remove: vi.fn(),
    refresh: vi.fn(),
    reconcileRequest: vi.fn(),
    resume: vi.fn(),
    dismissRejectedSubmission: vi.fn(),
    getSnapshot: () => snapshot,
    subscribe: () => () => {},
  }
}

describe('ServerMessageQueuePanel', () => {
  it('arms a queued steer locally and only promotes it after Send now', async () => {
    const queue = controller()
    render(
      <ServerMessageQueueProvider controller={queue}>
        <ServerMessageQueuePanel labels={labels} />
      </ServerMessageQueueProvider>,
    )
    const user = userEvent.setup()

    await user.click(screen.getAllByRole('button', { name: 'Steer' })[0])
    expect(mocks.officialSteer).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Send now' })).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Cancel steer' }))
    expect(mocks.officialSteer).not.toHaveBeenCalled()

    await user.click(screen.getAllByRole('button', { name: 'Steer' })[0])
    await user.click(screen.getByRole('button', { name: 'Send now' }))
    expect(mocks.officialSteer).toHaveBeenCalledOnce()
  })

  it('renders authoritative items and wires edit, reorder, and resume controls', async () => {
    const queue = controller()
    render(
      <ServerMessageQueueProvider controller={queue}>
        <ServerMessageQueuePanel labels={labels} />
      </ServerMessageQueueProvider>,
    )
    const user = userEvent.setup()

    expect(screen.getByText('Queue')).toBeVisible()
    expect(screen.getByText('Paused')).toBeVisible()
    expect(screen.getByText('Queued')).toBeVisible()
    await user.click(screen.getAllByRole('button', { name: 'More actions' })[0])
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.clear(screen.getByRole('textbox', { name: 'Edit' }))
    await user.type(screen.getByRole('textbox', { name: 'Edit' }), 'edited queue message')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await user.click(screen.getAllByRole('button', { name: 'More actions' })[0])
    await user.click(screen.getByRole('button', { name: 'Move down' }))
    await user.click(screen.getByRole('button', { name: 'Resume' }))

    expect(queue.edit).toHaveBeenCalledWith(
      'input-1',
      expect.objectContaining({ content: [{ type: 'text', text: 'edited queue message' }] }),
    )
    expect(queue.move).toHaveBeenCalledWith('input-1', {
      lane: 'queue',
      insertAfter: 'input-2',
    })
    expect(queue.resume).toHaveBeenCalledOnce()
  })

  it('restores rejected composer text, attachments, and run config without resubmitting', async () => {
    const queue = controller()
    const rejectedMessage = {
      role: 'user' as const,
      content: [{ type: 'text' as const, text: 'recover this draft' }],
      attachments: [
        {
          id: 'attachment-1',
          type: 'document' as const,
          name: 'notes.txt',
          contentType: 'text/plain',
          content: [],
          status: { type: 'complete' as const },
        },
      ],
      createdAt: new Date('2026-09-06T00:00:00Z'),
      parentId: null,
      sourceId: null,
      runConfig: { custom: { resource_context: ['original'], retained: true } },
      metadata: { custom: {} },
    }
    vi.spyOn(queue, 'getSnapshot').mockReturnValue({
      ...queue.getSnapshot(),
      lastOperation: { kind: 'failed', requestId: 'request-1', message: 'rejected' },
      rejectedSubmission: { message: rejectedMessage, strategy: 'enqueue' },
    })
    mocks.getComposerState.mockReturnValue({
      text: 'newer draft',
      runConfig: { custom: { newer: true } },
    })

    render(
      <ServerMessageQueueProvider controller={queue}>
        <ServerMessageQueuePanel labels={labels} />
      </ServerMessageQueueProvider>,
    )
    await userEvent.setup().click(screen.getByRole('button', { name: 'Restore draft' }))

    expect(mocks.setText).toHaveBeenCalledWith('recover this draft\nnewer draft')
    expect(mocks.setRunConfig).toHaveBeenCalledWith({
      custom: { resource_context: ['original'], retained: true, newer: true },
    })
    expect(mocks.addAttachment).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'attachment-1', name: 'notes.txt' }),
    )
    expect(queue.dismissRejectedSubmission).toHaveBeenCalledOnce()
    expect(queue.enqueue).not.toHaveBeenCalled()
    expect(queue.steer).not.toHaveBeenCalled()
  })
})
