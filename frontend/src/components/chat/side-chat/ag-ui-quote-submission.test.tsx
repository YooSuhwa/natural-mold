import { cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'
import { installChatApi, threadId } from '../../../../tests/ag-ui/ordinary-chat-api'
import { csrfStore } from '@/lib/auth/csrf'
import { AgUiRuntimeSection } from '../ag-ui-runtime-section'
import { SideChatContext, type MessageQuote } from './side-chat-context'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  csrfStore.clear()
})

it('preserves a quoted side draft during hydration and sends it once when the side thread is ready', async () => {
  const quote: MessageQuote = {
    kind: 'conversation',
    id: 'a8843220-be88-4b7b-a871-67c18a399722',
    message_id: 'main-answer',
    message_role: 'assistant',
    quote: '본 대화에서 선택한 문장',
  }
  const text = '선택한 부분을 자세히 알려 주세요.'
  csrfStore.set('synthetic-csrf')
  const api = installChatApi()
  const fetchFixture = window.fetch
  const snapshot = Promise.withResolvers<void>()
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const response = await fetchFixture(input, init)
    if (String(input).includes('/ag-ui/snapshot')) await snapshot.promise
    return response
  })
  const user = userEvent.setup()
  render(
    <SideChatContext.Provider
      value={{
        mainId: quote.id,
        mainTitle: '본 대화',
        sideId: threadId,
        open: true,
        loading: false,
        error: false,
        delivery: { id: 1, target: 'side', reference: quote },
        draft: { current: null },
        show: vi.fn(),
        close: vi.fn(),
        deliver: vi.fn(),
        acknowledge: vi.fn(),
      }}
    >
      <AgUiRuntimeSection
        agentId="synthetic-agent"
        conversationId={threadId}
        serverMessages={[]}
        serverLatestRun={null}
        onRuntimeStatusChange={vi.fn()}
        onStreamEnd={vi.fn()}
        threadProps={{ conversationId: threadId, emptyContent: null, compact: true }}
      />
    </SideChatContext.Provider>,
  )
  try {
    await waitFor(() => expect(api.snapshotReads).toHaveBeenCalled())
    expect(await screen.findByTestId('quote-context-chips')).toBeVisible()
    const field = screen.getByRole('textbox')
    const send = screen.getByRole('button', { name: '전송', exact: true })
    await user.type(field, text + '{Enter}')
    expect(send).toBeDisabled()
    expect(field).toHaveValue(text)
    expect(api.commands).toHaveLength(0)

    snapshot.resolve()
    await waitFor(() => expect(send).toBeEnabled())
    await user.keyboard('{Enter}')
    await waitFor(() => expect(api.commands).toHaveLength(1))
    expect(api.commands[0]).toMatchObject({
      threadId,
      messages: [{ role: 'user', content: text }],
      forwardedProps: { moldy: { resourceContext: { resources: [quote] } } },
    })
    expect(api.writes).toEqual([`/api/conversations/${threadId}/ag-ui/runs`])
  } finally {
    snapshot.resolve()
  }
})
