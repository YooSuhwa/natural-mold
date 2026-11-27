import type { HttpAgent } from '@ag-ui/client'
import {
  AssistantRuntimeProvider,
  ThreadPrimitive,
  fromThreadMessageLike,
  type RunConfig,
  type ThreadHistoryAdapter,
} from '@assistant-ui/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
import { act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EditButton, RegenerateButton, UserMessageEditor } from '../assistant-message-actions'
import { ChatConversationContext } from '../conversation-context'
import { AgUiChatActionContext, type AgUiChatActions } from '@/lib/ag-ui/chat-action-context'
import { conversationId, createFixtureAgent } from '../../../../tests/ag-ui/chat-ui-fixtures'
import { render, screen, userEvent, waitFor, within } from '../../../../tests/test-utils'

const history: ThreadHistoryAdapter = {
  load: async () => {
    const transcript = [
      ['user', 'earlier-question'],
      ['assistant', 'earlier-answer'],
      ['user', 'latest-question'],
      ['assistant', 'latest-answer'],
    ] as const
    return {
      headId: 'latest-answer',
      messages: transcript.map(([role, id], index) => ({
        parentId: transcript[index - 1]?.[1] ?? null,
        message: fromThreadMessageLike(
          { id, role, content: id, metadata: { custom: { agui: { messageId: 'wire-' + id } } } },
          id,
          { type: 'complete', reason: 'stop' },
        ),
      })),
    }
  },
  append: async () => {},
}

function Harness({ agent, actions }: { agent: HttpAgent; actions: AgUiChatActions }) {
  const runtime = useAgUiRuntime({ agent, adapters: { history } })
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ChatConversationContext.Provider value={conversationId}>
        <AgUiChatActionContext.Provider value={actions}>
          <ThreadPrimitive.Messages>
            {({ message }) => (
              <section aria-label={message.id}>
                {message.role === 'assistant' ? (
                  <RegenerateButton />
                ) : message.composer.isEditing ? (
                  <UserMessageEditor />
                ) : (
                  <EditButton />
                )}
              </section>
            )}
          </ThreadPrimitive.Messages>
        </AgUiChatActionContext.Provider>
      </ChatConversationContext.Provider>
    </AssistantRuntimeProvider>
  )
}

function mount() {
  const { agent, fetch } = createFixtureAgent(() => [])
  let resolve!: (value: RunConfig) => void
  let reject!: (reason: Error) => void
  const pending = new Promise<RunConfig>((yes, no) => {
    resolve = yes
    reject = no
  })
  const getBranchRunConfig = vi.fn<AgUiChatActions['getBranchRunConfig']>(() => pending)
  const actions: AgUiChatActions = {
    shouldQueue: false,
    queueDraft: vi.fn(),
    stopActiveRun: vi.fn(),
    getBranchRunConfig,
  }
  const { unmount } = render(<Harness agent={agent} actions={actions} />)
  return { fetch, getBranchRunConfig, resolve, reject, unmount }
}

async function clickAction(operation: 'edit' | 'regenerate') {
  const user = userEvent.setup()
  const id = operation === 'edit' ? 'earlier-question' : 'earlier-answer'
  const row = within(await screen.findByRole('region', { name: id }))
  if (operation === 'edit') {
    await user.click(row.getByRole('button', { name: '편집' }))
    const input = row.getByRole('textbox')
    await user.clear(input)
    await user.type(input, 'revised earlier question')
    await user.click(row.getByRole('button', { name: '저장' }))
  } else {
    await user.click(row.getByRole('button', { name: '재생성' }))
  }
  return id
}

describe('AG-UI branch controls on restored history', () => {
  afterEach(() => vi.restoreAllMocks())

  it.each(['edit', 'regenerate'] as const)(
    'runs %s once with the selected checkpoint',
    async (op) => {
      const { fetch, resolve, getBranchRunConfig } = mount()
      const id = await clickAction(op)
      expect(getBranchRunConfig).toHaveBeenCalledOnce()
      expect(getBranchRunConfig.mock.calls[0]?.slice(0, 2)).toMatchObject([
        op,
        { id, metadata: { custom: { agui: { messageId: 'wire-' + id } } } },
      ])
      expect(fetch).not.toHaveBeenCalled()

      const moldy = {
        operation: op,
        checkpointId: 'before-first-answer',
        expectedHead: 'last-head',
      }
      await act(async () => resolve({ custom: { moldy } }))
      await waitFor(() => expect(fetch).toHaveBeenCalledOnce())
      const request = JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))
      expect(request.forwardedProps).toEqual({ moldy })
      expect(request.messages).toEqual(
        op === 'regenerate'
          ? []
          : [expect.objectContaining({ role: 'user', content: 'revised earlier question' })],
      )
    },
  )

  it('keeps the edit draft when the checkpoint cannot be resolved', async () => {
    const { fetch, reject } = mount()
    await clickAction('edit')
    await act(async () => reject(new Error('Checkpoint unavailable')))
    expect(screen.getByRole('textbox')).toHaveValue('revised earlier question')
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each(['edit', 'regenerate'] as const)(
    'drops a pending %s when leaving this chat',
    async (op) => {
      const { fetch, resolve, unmount } = mount()
      await clickAction(op)
      unmount()
      await act(async () =>
        resolve({ custom: { moldy: { operation: op, checkpointId: 'stale' } } }),
      )
      expect(fetch).not.toHaveBeenCalled()
    },
  )
})
