import { cleanup } from '@testing-library/react'
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
    await user.click(await screen.findByRole('button', { name: '도움이 됨' }))
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
    oldRead.resolve()
  }
})
