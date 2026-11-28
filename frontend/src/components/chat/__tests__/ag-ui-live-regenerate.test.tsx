import { act, cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'
import {
  answer,
  installChatApi,
  question,
  storedAnswerId,
  threadId,
} from '../../../../tests/ag-ui/ordinary-chat-api'
import { csrfStore } from '@/lib/auth/csrf'
import { dispatchMoldyBranchSwitched } from '@/lib/chat/langgraph-runtime/branch-switch-events'
import { AgUiRuntimeSection } from '../ag-ui-runtime-section'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  csrfStore.clear()
})

type BranchSnapshot = {
  activeTipMessageId: string
  checkpointId: string
  messages: { id: string; role: string; content: string }[]
  messageLinks: { messageId: string; uiMessageId: string; parentCheckpointId: string | null }[]
}

it.each(['ready', 'missing its parent checkpoint', 'switched before confirmation'] as const)(
  'checks the saved reply before regenerating when the branch is %s',
  async (state) => {
    csrfStore.set('synthetic-csrf')
    const api = installChatApi({ textOnly: true })
    const serve = globalThis.fetch
    const history = Promise.withResolvers<void>()
    const proof = Promise.withResolvers<void>()
    const historyStarted = vi.fn()
    const proofStarted = vi.fn()
    let switched = false
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await serve(input, init)
      const path = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
        .pathname
      if (api.commands.length !== 1 || !path.endsWith('/ag-ui/snapshot')) return response
      const snapshot: BranchSnapshot = await response.json()
      const reply = snapshot.messageLinks.find((link) => link.messageId === 'assistant-done')
      if (!reply) throw new Error('Missing saved assistant reply')
      reply.parentCheckpointId =
        state === 'missing its parent checkpoint' ? null : 'checkpoint-before-answer'
      if (!historyStarted.mock.calls.length) {
        historyStarted()
        await history.promise
      } else if (switched) {
        snapshot.checkpointId = 'another-checkpoint'
        snapshot.activeTipMessageId = 'another-reply'
        snapshot.messages = snapshot.messages.map((message) =>
          message.role === 'assistant' ? { ...message, id: 'another-reply' } : message,
        )
        reply.messageId = 'another-reply'
        reply.uiMessageId = 'another-saved-reply'
      } else {
        proofStarted()
        await proof.promise
      }
      return Response.json(snapshot)
    })
    render(
      <AgUiRuntimeSection
        agentId="synthetic-agent"
        conversationId={threadId}
        serverMessages={[]}
        serverLatestRun={null}
        onRuntimeStatusChange={vi.fn()}
        onStreamEnd={vi.fn()}
        threadProps={{ conversationId: threadId }}
      />,
    )
    try {
      const user = userEvent.setup()
      await user.type(await screen.findByRole('textbox'), question)
      await user.click(screen.getByRole('button', { name: '전송' }))
      expect(await screen.findByText(answer)).toBeVisible()
      await waitFor(() => expect(historyStarted).toHaveBeenCalledOnce())
      const reload = () => screen.getByRole('button', { name: '재생성' })
      expect(reload()).toBeDisabled()
      await user.click(reload())
      expect(proofStarted).not.toHaveBeenCalled()
      expect(api.commands).toHaveLength(1)

      const assertSavedReply = (id: string) =>
        waitFor(() =>
          expect(screen.getByText(answer).closest('[data-moldy-message-id]')).toHaveAttribute(
            'data-moldy-message-id',
            id,
          ),
        )
      await act(async () => history.resolve())
      await assertSavedReply(storedAnswerId)
      if (state === 'missing its parent checkpoint') {
        expect(reload()).toBeDisabled()
        await user.click(reload())
        expect(proofStarted).not.toHaveBeenCalled()
        expect(api.commands).toHaveLength(1)
        return
      }
      expect(reload()).toBeEnabled()
      await user.click(reload())
      await waitFor(() => expect(proofStarted).toHaveBeenCalledOnce())
      if (state === 'switched before confirmation') {
        switched = true
        act(() =>
          dispatchMoldyBranchSwitched({
            conversationId: threadId,
            checkpointId: 'another-checkpoint',
          }),
        )
        await assertSavedReply('another-saved-reply')
      }
      await act(async () => proof.resolve())
      if (switched) {
        expect(api.commands).toHaveLength(1)
      } else {
        await waitFor(() => expect(api.commands).toHaveLength(2))
        expect(api.commands[1]).toMatchObject({
          messages: [],
          forwardedProps: {
            moldy: {
              operation: 'regenerate',
              checkpointId: 'checkpoint-before-answer',
              expectedHead: 'checkpoint-A',
            },
          },
        })
      }
    } finally {
      history.resolve()
      proof.resolve()
    }
  },
)
