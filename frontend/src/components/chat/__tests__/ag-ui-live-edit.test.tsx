import { act, cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor, within } from '../../../../tests/test-utils'
import {
  answer,
  installChatApi,
  question,
  threadId,
} from '../../../../tests/ag-ui/ordinary-chat-api'
import { csrfStore } from '@/lib/auth/csrf'
import { AgUiRuntimeSection } from '../ag-ui-runtime-section'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  csrfStore.clear()
})

const oldQuestion = '저장된 첫 질문'
const revised = '새로 쓴 질문'

function savedTurn(id: string, content: string, response: string, parent: string | null) {
  const messages = [
    { id, role: 'user', content },
    { id: id + '-answer', role: 'assistant', content: response },
  ]
  const messageLinks = messages.map((message, index) => ({
    messageId: message.id,
    uiMessageId: message.id + '-stored',
    parentMessageId: index === 0 ? parent : id,
    checkpointId: id + '-head',
    // A canonical head need not itself be an editable message.
    parentCheckpointId: index === 0 ? id + '-parent' : null,
    siblingCheckpointIds: [] as string[],
  }))
  return { messages, messageLinks }
}

function editButton(text: string) {
  const row = screen.getByText(text).closest<HTMLElement>('[data-moldy-message-role="user"]')
  if (!row) throw new Error('Missing user message')
  return within(row).getByRole('button', { name: '편집' })
}

it.each(['new', 'older'] as const)(
  'waits for the displayed head before editing the %s user message',
  async (target) => {
    csrfStore.set('synthetic-csrf')
    const api = installChatApi({ textOnly: true })
    const serve = globalThis.fetch
    const history = Promise.withResolvers<void>()
    const historyStarted = vi.fn()
    const text = target === 'older' ? oldQuestion : question
    const targetId = target === 'older' ? 'older' : 'canonical-input'
    const older = savedTurn('older', oldQuestion, '첫 응답', null)
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await serve(input, init)
      const path = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
        .pathname
      if (!path.endsWith('/ag-ui/snapshot')) return response
      const base: Record<string, unknown> = await response.json()
      const count = api.commands.length
      if (!count) {
        return target === 'older'
          ? Response.json({
              ...base,
              ...older,
              checkpointId: 'older-head',
              activeTipMessageId: 'older-answer',
            })
          : Response.json(base)
      }
      const edited = count === 2
      const id = edited ? 'edited-input' : 'canonical-input'
      const current = savedTurn(
        id,
        edited ? revised : question,
        answer,
        target === 'older' && !edited ? 'older-answer' : null,
      )
      if (edited)
        current.messageLinks[0].siblingCheckpointIds = ['canonical-input-head', 'edited-input-head']
      const prefix = target === 'older' && !edited ? older : { messages: [], messageLinks: [] }
      if (count === 1 && !historyStarted.mock.calls.length) {
        historyStarted()
        await history.promise
      }
      return Response.json({
        ...base,
        checkpointId: id + '-head',
        activeTipMessageId: id + '-answer',
        messages: [...prefix.messages, ...current.messages],
        messageLinks: [...prefix.messageLinks, ...current.messageLinks],
      })
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
      if (target === 'older') {
        expect(await screen.findByText(oldQuestion)).toBeVisible()
        expect(editButton(oldQuestion)).toBeEnabled()
      }
      const user = userEvent.setup()
      await user.type(await screen.findByRole('textbox'), question)
      await user.click(screen.getByRole('button', { name: '전송' }))
      expect(await screen.findByText(answer)).toBeVisible()
      await waitFor(() => expect(historyStarted).toHaveBeenCalledOnce())
      expect(editButton(text)).toBeDisabled()
      await user.click(editButton(text))
      expect(screen.queryByRole('button', { name: '저장' })).not.toBeInTheDocument()
      expect(api.commands).toHaveLength(1)
      await act(async () => history.resolve())
      await waitFor(() =>
        expect(screen.getByText(answer).closest('[data-moldy-message-id]')).toHaveAttribute(
          'data-moldy-message-id',
          'canonical-input-answer-stored',
        ),
      )
      await waitFor(() => expect(editButton(text)).toBeEnabled())
      expect(api.commands[0].messages.at(-1)?.id).not.toBe('canonical-input')
      await user.click(editButton(text))
      const editor = screen.getByDisplayValue(text)
      await user.clear(editor)
      await user.type(editor, revised)
      await user.click(screen.getByRole('button', { name: '저장' }))
      await waitFor(() => expect(api.commands).toHaveLength(2))
      expect(api.commands[1]).toMatchObject({
        messages: [{ role: 'user', content: revised }],
        forwardedProps: {
          moldy: {
            operation: 'edit',
            checkpointId: targetId + '-parent',
            expectedHead: 'canonical-input-head',
          },
        },
      })
      expect(await screen.findByText('2/2')).toBeVisible()
      expect(screen.getByText(revised)).toBeVisible()
    } finally {
      history.resolve()
    }
  },
)
