import { cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor, within } from '../../../../tests/test-utils'
import {
  answer,
  installChatApi,
  question,
  storedAnswerId,
  threadId,
} from '../../../../tests/ag-ui/ordinary-chat-api'
import { csrfStore } from '@/lib/auth/csrf'
import {
  publicMessageIdMap,
  resolveFeedbackMessageId,
  useChatFeedbackAdapter,
} from '@/lib/chat/feedback-adapter'
import { useMessages, useFetchMessagesEnvelope } from '@/lib/hooks/use-conversations'
import { AgUiRuntimeSection } from '../ag-ui-runtime-section'

function ChatWithFeedback({ onRefresh }: { onRefresh: () => void }) {
  const messages = useMessages(threadId)
  const currentIds = publicMessageIdMap(messages.data ?? [])
  const readEnvelope = useFetchMessagesEnvelope()
  const feedback = useChatFeedbackAdapter(threadId, () => undefined, {
    resolveMessageId: (id) => resolveFeedbackMessageId(currentIds, id),
    refreshMessageId: async (id) => {
      onRefresh()
      const envelope = await readEnvelope(threadId)
      const stored = resolveFeedbackMessageId(publicMessageIdMap(envelope.messages), id)
      return stored ? { id: stored, rating: undefined } : undefined
    },
  })
  return (
    <AgUiRuntimeSection
      agentId="synthetic-agent"
      conversationId={threadId}
      serverMessages={messages.data ?? []}
      serverLatestRun={null}
      feedbackAdapter={feedback}
      onRuntimeStatusChange={vi.fn()}
      onStreamEnd={vi.fn()}
      threadProps={{ conversationId: threadId }}
    />
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  csrfStore.clear()
})

it('saves one positive rating on a live reply while an older messages read is still in flight', async () => {
  csrfStore.set('synthetic-csrf')
  const api = installChatApi({ textOnly: true })
  const runApi = globalThis.fetch
  const oldRead = Promise.withResolvers<void>()
  const releaseSelectedHistory = Promise.withResolvers<void>()
  const selectedHistoryArrived = vi.fn()
  const savedMessage = {
    id: storedAnswerId,
    runtime_message_id: 'assistant-done',
    role: 'assistant',
    content: answer,
  }
  const messageReads = vi.fn()
  const votes = vi.fn()
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
      .pathname
    const method = init?.method ?? 'GET'
    if (method === 'GET' && path.endsWith('/ag-ui/snapshot') && api.commands.length) {
      // The browser has seen the SDK's terminal frame. The selected history that assigns
      // a public UUID to the answer has not yet been delivered to the transcript.
      const response = await runApi(input, init)
      selectedHistoryArrived()
      await releaseSelectedHistory.promise
      return response
    }
    if (method === 'GET' && path === '/api/conversations/' + threadId + '/messages') {
      messageReads()
      if (messageReads.mock.calls.length === 1) {
        // The page opened before this conversation had messages. Its earlier read is still returning.
        await oldRead.promise
        return Response.json({ messages: [] })
      }
      return Response.json({ messages: [savedMessage] })
    }
    if (path.endsWith('/feedback')) {
      votes(
        path,
        method,
        JSON.parse(String(init?.body)),
        init?.credentials,
        new Headers(init?.headers).get('X-CSRF-Token'),
      )
      return Response.json({ message_id: storedAnswerId, rating: 'up', conversation_id: threadId })
    }
    return runApi(input, init)
  })
  try {
    const refreshing = vi.fn()
    render(<ChatWithFeedback onRefresh={refreshing} />)
    await waitFor(() => expect(messageReads).toHaveBeenCalledTimes(1))
    const user = userEvent.setup()
    await user.type(await screen.findByRole('textbox'), question)
    await user.click(screen.getByRole('button', { name: '전송' }))
    expect(await screen.findByText(answer)).toBeVisible()
    expect(api.commands).toHaveLength(1)
    expect(votes).not.toHaveBeenCalled()
    await waitFor(() => expect(selectedHistoryArrived).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: '도움이 됨' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '도움이 안 됨' })).toBeDisabled()
    releaseSelectedHistory.resolve()
    await waitFor(() => expect(screen.getByRole('button', { name: '도움이 됨' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: '도움이 됨' }))
    await waitFor(() => expect(refreshing).toHaveBeenCalledOnce())
    oldRead.resolve()
    await waitFor(() =>
      expect(votes).toHaveBeenCalledExactlyOnceWith(
        '/api/messages/' + storedAnswerId + '/feedback',
        'POST',
        { rating: 'up', conversation_id: threadId },
        'include',
        'synthetic-csrf',
      ),
    )
    expect(api.commands).toHaveLength(1)
  } finally {
    releaseSelectedHistory.resolve()
    oldRead.resolve()
  }
})

it('rates an older stored answer on the selected branch after opening saved history', async () => {
  csrfStore.set('synthetic-csrf')
  const api = installChatApi()
  const runApi = globalThis.fetch
  const turns = [
    { role: 'user', content: '첫 질문' },
    { role: 'assistant', content: '첫 답변' },
    { role: 'user', content: '다음 질문' },
    { role: 'assistant', content: '최근 답변' },
  ]
  const rows = turns.map((turn, index) => ({
    ...turn,
    id: crypto.randomUUID(),
    runtime_message_id: 'wire-' + index,
    conversation_id: threadId,
    tool_calls: null,
    tool_call_id: null,
    created_at: '2026-11-27T00:00:00Z',
  }))
  const votes = vi.fn()
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const address = new URL(
      input instanceof Request ? input.url : String(input),
      'http://localhost',
    )
    if (address.pathname.endsWith('/ag-ui/snapshot')) {
      const base = await (await runApi(input, init)).json()
      return Response.json({
        ...base,
        checkpointId: 'last-checkpoint',
        activeTipMessageId: rows[3].runtime_message_id,
        messages: rows.map((row) => ({
          id: row.runtime_message_id,
          role: row.role,
          content: row.content,
        })),
        messageLinks: rows.map((row, index) => ({
          messageId: row.runtime_message_id,
          uiMessageId: row.id,
          parentMessageId: rows[index - 1]?.runtime_message_id ?? null,
          checkpointId: 'saved-' + index,
          runId: index < 2 ? 'earlier-run' : 'later-run',
        })),
      })
    }
    if (address.pathname === '/api/conversations/' + threadId + '/messages')
      return Response.json({ messages: rows })
    if (address.pathname.endsWith('/feedback')) {
      votes(address.pathname, init?.method, JSON.parse(String(init?.body)))
      return Response.json({ message_id: rows[1].id, rating: 'up', conversation_id: threadId })
    }
    return runApi(input, init)
  })
  render(<ChatWithFeedback onRefresh={vi.fn()} />)
  const text = await screen.findByText('첫 답변')
  const older = text.closest<HTMLElement>('[data-moldy-message-role="assistant"]')
  if (!older) throw new Error('Missing the first saved assistant row')
  expect(older).toHaveAttribute('data-moldy-message-id', rows[1].id)
  const positive = within(older).getByRole('button', { name: '도움이 됨' })
  expect(positive).toBeEnabled()
  await userEvent.setup().click(positive)
  await waitFor(() =>
    expect(votes).toHaveBeenCalledExactlyOnceWith(
      '/api/messages/' + rows[1].id + '/feedback',
      'POST',
      { rating: 'up', conversation_id: threadId },
    ),
  )
  expect(api.commands).toHaveLength(0)
})
