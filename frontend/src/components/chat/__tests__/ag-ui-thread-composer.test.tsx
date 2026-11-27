import { AssistantRuntimeProvider, useAuiState } from '@assistant-ui/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
import { act, fireEvent } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AgUiChatActionContext, type AgUiChatActions } from '@/lib/ag-ui/chat-action-context'
import { agUiAttachmentAdapter } from '@/lib/ag-ui/chat-attachment-adapter'
import * as uploads from '@/lib/api/uploads'
import { moldyAttachmentAdapter } from '@/lib/chat/attachment-adapter'
import * as clientLogger from '@/lib/logging/client-logger'
import { conversationId, createFixtureAgent } from '../../../../tests/ag-ui/chat-ui-fixtures'
import { server } from '../../../../tests/setup'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'
import { ThreadComposer } from '../assistant-thread-composer'
import { ChatConversationContext } from '../conversation-context'

const uploadId = '25976b8a-4046-47a3-b95f-7e17063b1803'
const file = new File(['private attachment bytes'], 'invoice.txt', { type: 'text/plain' })
const uploaded = {
  id: uploadId,
  filename: file.name,
  mime_type: file.type,
  size_bytes: file.size,
  url: '/uploads/invoice',
  created_at: '2026-11-27T00:00:00Z',
}

function setupComposer(shouldQueue: boolean) {
  const { agent, fetch } = createFixtureAgent(() => [])
  const actions = {
    shouldQueue,
    canStopActiveRun: shouldQueue,
    attachmentAdapter: agUiAttachmentAdapter(moldyAttachmentAdapter),
    queueDraft: vi.fn<AgUiChatActions['queueDraft']>().mockResolvedValue(undefined),
    stopActiveRun: vi.fn<AgUiChatActions['stopActiveRun']>().mockResolvedValue(undefined),
    getBranchRunConfig: vi.fn<AgUiChatActions['getBranchRunConfig']>(),
  }
  function ActiveComposer() {
    const isRunning = useAuiState((state) => state.thread.isRunning)
    return (
      <AgUiChatActionContext.Provider value={{ ...actions, shouldQueue: shouldQueue || isRunning }}>
        <ThreadComposer enableAttachments />
      </AgUiChatActionContext.Provider>
    )
  }
  function Chat() {
    const runtime = useAgUiRuntime({ agent, adapters: { attachments: actions.attachmentAdapter } })
    return (
      <AssistantRuntimeProvider runtime={runtime}>
        <ChatConversationContext.Provider value={conversationId}>
          <ActiveComposer />
        </ChatConversationContext.Provider>
      </AssistantRuntimeProvider>
    )
  }
  return { ...render(<Chat />), user: userEvent.setup(), actions, fetch }
}

async function pasteAttachment() {
  fireEvent.paste(screen.getByRole('textbox'), { clipboardData: { files: [file] } })
  expect(await screen.findByText(file.name)).toBeVisible()
}

beforeEach(() => {
  server.use(
    http.get('*/api/artifacts', () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get('*/api/conversations/:id/files', () => HttpResponse.json([])),
    http.post('*/api/conversations/:id/followup-suggestion', () =>
      HttpResponse.json({ suggestion: null }),
    ),
  )
})
afterEach(() => vi.restoreAllMocks())

describe('normal AG-UI chat composer', () => {
  it('sends a file-only draft through the official runtime using the confirmed upload ID', async () => {
    const upload = vi.spyOn(uploads, 'uploadFile').mockResolvedValue(uploaded)
    const { user, fetch, actions } = setupComposer(false)
    await pasteAttachment()
    expect(upload).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '전송' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    const body = String(fetch.mock.calls[0][1]?.body)
    expect(JSON.parse(body)).toMatchObject({
      threadId: conversationId,
      messages: [
        {
          role: 'user',
          content: [
            {
              type: 'document',
              source: {
                type: 'file',
                value: uploadId,
                mimeType: file.type,
                provider: 'moldy',
              },
            },
          ],
        },
      ],
      forwardedProps: { moldy: { attachmentIds: [uploadId] } },
    })
    expect(body).not.toMatch(/local-|private attachment bytes|\/uploads\/invoice/)
    expect(upload).toHaveBeenCalledTimes(1)
    expect(actions.queueDraft).not.toHaveBeenCalled()
  })

  it.each(['Enter', 'form submit'])(
    'queues %s once and preserves edits during acknowledgement',
    async (entry) => {
      const { user, actions, fetch } = setupComposer(true)
      const receipt = Promise.withResolvers<void>()
      actions.queueDraft.mockReturnValueOnce(receipt.promise)
      const input = screen.getByRole('textbox')
      await user.type(input, 'queue this')
      if (entry === 'Enter') {
        await user.keyboard('{Enter}')
      } else {
        act(() => input.closest('form')?.requestSubmit())
      }
      await waitFor(() => expect(actions.queueDraft).toHaveBeenCalledTimes(1))
      expect(actions.queueDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          role: 'user',
          content: [{ type: 'text', text: 'queue this' }],
        }),
        false,
      )
      expect(input).toHaveValue('queue this')
      await user.type(input, ' plus a later edit{Enter}')
      await act(async () => receipt.resolve())
      expect(input).toHaveValue('queue this plus a later edit')
      expect(actions.queueDraft).toHaveBeenCalledTimes(1)
      expect(fetch).not.toHaveBeenCalled()
    },
  )

  it('keeps queued input on the server when the active official runtime finishes', async () => {
    const { user, actions, fetch } = setupComposer(false)
    const respond = fetch.getMockImplementation()
    if (!respond) throw new Error('A fixture response is required')
    const hold = Promise.withResolvers<void>()
    fetch.mockImplementationOnce(async (...args) => {
      await hold.promise
      return respond(...args)
    })
    const input = screen.getByRole('textbox')
    await user.type(input, 'first request{Enter}')
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce())
    expect(await screen.findByRole('button', { name: '다음 요청으로 대기' })).toBeVisible()
    await user.type(input, 'second request{Enter}')
    await waitFor(() => expect(actions.queueDraft).toHaveBeenCalledOnce())
    await act(async () => hold.resolve())
    expect(await screen.findByRole('button', { name: '전송' })).toBeVisible()
    expect(input).toHaveValue('')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('keeps a rejected file-only draft and reuses its upload on the confirmed interrupt', async () => {
    const upload = vi.spyOn(uploads, 'uploadFile').mockResolvedValue(uploaded)
    const warning = vi.spyOn(clientLogger, 'reportClientWarning').mockImplementation(() => {})
    const { user, actions, fetch } = setupComposer(true)
    actions.queueDraft
      .mockRejectedValueOnce(new Error('unconfirmed receipt'))
      .mockRejectedValueOnce(new Error('still unconfirmed'))
    await pasteAttachment()
    await user.click(screen.getByRole('button', { name: '다음 요청으로 대기' }))
    await waitFor(() => expect(actions.queueDraft).toHaveBeenCalledTimes(1))
    const retry = () => screen.getByRole('button', { name: '현재 응답을 중단하고 바로 전송' })
    await waitFor(() => expect(retry()).toBeEnabled())
    expect(warning).toHaveBeenCalledTimes(1)
    expect(screen.getByText(file.name)).toBeVisible()
    await user.click(screen.getByRole('button', { name: '다음 요청으로 대기' }))
    await waitFor(() => expect(warning).toHaveBeenCalledTimes(2))
    const [first, second] = actions.queueDraft.mock.calls
    // The queue controller keys its idempotency token by submitted message identity.
    expect(second[0]).toBe(first[0])
    await user.click(retry())
    await user.click(screen.getByRole('button', { name: '즉시 전송 취소' }))
    expect(actions.queueDraft).toHaveBeenCalledTimes(2)
    await user.click(retry())
    await user.click(screen.getByRole('button', { name: '지금 보내기' }))
    await waitFor(() => expect(screen.queryByText(file.name)).not.toBeInTheDocument())
    expect(
      actions.queueDraft.mock.calls.map(([draft, interrupt]) => ({
        ids: draft.attachments?.map((attachment) => attachment.id),
        interrupt,
      })),
    ).toEqual([
      { ids: [uploadId], interrupt: false },
      { ids: [uploadId], interrupt: false },
      { ids: [uploadId], interrupt: true },
    ])
    expect(upload).toHaveBeenCalledTimes(1)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('drops a removed selection before its upload finishes without queuing an empty message', async () => {
    const pending = Promise.withResolvers<typeof uploaded>()
    const upload = vi.spyOn(uploads, 'uploadFile').mockReturnValue(pending.promise)
    const { user, actions, fetch } = setupComposer(true)
    await pasteAttachment()
    await user.click(screen.getByRole('button', { name: '다음 요청으로 대기' }))
    await waitFor(() => expect(upload).toHaveBeenCalledOnce())
    await user.click(screen.getByRole('button', { name: '첨부 파일 제거' }))
    await act(async () => pending.resolve(uploaded))
    expect(actions.queueDraft).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
    expect(screen.queryByText(file.name)).not.toBeInTheDocument()
  })

  it.each(['stop button', '/stop'])('sends one explicit server stop using %s', async (entry) => {
    const { user, actions, fetch, unmount } = setupComposer(true)
    expect(actions.stopActiveRun).not.toHaveBeenCalled()
    if (entry === '/stop') {
      await user.type(screen.getByRole('textbox'), '/stop{Enter}')
    } else {
      await user.click(screen.getByRole('button', { name: '중단' }))
    }
    unmount()
    expect(actions.stopActiveRun).toHaveBeenCalledTimes(1)
    expect(actions.queueDraft).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  })
})
