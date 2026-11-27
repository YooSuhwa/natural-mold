import { cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { installChatApi, threadId } from '../../../../tests/ag-ui/ordinary-chat-api'
import { render, screen, userEvent, waitFor, within } from '../../../../tests/test-utils'
import { csrfStore } from '@/lib/auth/csrf'
import { queuedInput } from '@/lib/chat/message-queue/__tests__/server-message-queue-test-fixtures'
import { AgUiRuntimeSection } from '../ag-ui-runtime-section'

function openChat() {
  return render(
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
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  csrfStore.clear()
})

it('restores both durable inputs and promotes the selected revision only after confirmation', async () => {
  csrfStore.set('synthetic-csrf')
  const chat = installChatApi()
  const otherRequests = globalThis.fetch
  const queuePath = `/api/conversations/${threadId}/run-inputs`
  const drafts = [
    { id: '322de253-d870-4483-8573-b9c9a20df163', text: '지난달 보고서 초안' },
    { id: '284bd07e-7f7d-4db5-a10e-1286d0fed793', text: '이번 달 비교 표' },
  ]
  let items = drafts.map(({ id, text }, index) =>
    queuedInput({
      id,
      conversation_id: threadId,
      client_request_id: `agui:queued-${index}`,
      position: index + 1,
      revision: index + 6,
      attachment_ids: [],
      input_payload: { messages: [{ role: 'user', content: text }] },
    }),
  )
  const selected = items[1]
  const listReads = vi.fn()
  const promotions = vi.fn()
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
      .pathname
    const method = init?.method ?? 'GET'
    if (path === queuePath && method === 'GET') {
      listReads()
      return Response.json({ queue_paused: true, items })
    }
    if (path === `${queuePath}/${selected.id}/promote` && method === 'POST') {
      const body: unknown = JSON.parse(String(init?.body))
      promotions(body)
      expect(init?.credentials).toBe('include')
      expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
      const promoted = { ...selected, priority: 100, revision: selected.revision + 1 }
      items = items.map((item) => (item.id === selected.id ? promoted : item))
      return Response.json(promoted)
    }
    return otherRequests(input, init)
  })

  async function expectStoredRows() {
    for (const { id, text } of drafts) {
      const label = await screen.findByText(text, { exact: true })
      expect(label.closest('li')).toHaveAttribute('data-moldy-queue-item', id)
      expect(label).toBeVisible()
    }
    expect(document.querySelectorAll('[data-moldy-queue-item]')).toHaveLength(2)
  }

  const first = openChat()
  await expectStoredRows()
  first.unmount()
  const readsBeforeReopening = listReads.mock.calls.length
  openChat()
  await expectStoredRows()
  expect(listReads.mock.calls.length).toBeGreaterThan(readsBeforeReopening)
  expect(promotions).not.toHaveBeenCalled()
  expect(chat.writes).toEqual([])

  const row = () => {
    const element = screen.getByText(drafts[1].text).closest('li')
    if (!element) throw new Error('The queued input has no row')
    return element
  }
  const user = userEvent.setup()
  await user.click(within(row()).getByRole('button', { name: '현재 응답을 중단하고 먼저 실행' }))
  expect(promotions).not.toHaveBeenCalled()
  await user.click(within(row()).getByRole('button', { name: '지금 보내기' }))
  await waitFor(() => expect(promotions.mock.calls).toEqual([[{ expected_revision: 7 }]]))
  await waitFor(() =>
    expect(within(row()).queryByRole('button', { name: '지금 보내기' })).not.toBeInTheDocument(),
  )
  await expectStoredRows()
  expect(within(row()).queryByRole('button', { name: '현재 응답을 중단하고 먼저 실행' })).toBeNull()
  expect(chat.writes).toEqual([])
})
