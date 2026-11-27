import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type ThreadMessage,
} from '@assistant-ui/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createStore, Provider as JotaiProvider } from 'jotai'
import type { ReactNode } from 'react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import messages from '../../../../messages/en.json'
import { createTestQueryClient } from '../../../../tests/test-utils'
import { ChatConversationContext } from '../conversation-context'
import { DelegatedChildTasks } from '../delegated-child-tasks'
import { conversationQueryKeys } from '@/lib/query-keys/conversations'
import { currentOrgScope } from '@/lib/org-scope/org-scope-signal'
import { ApiError } from '@/lib/api/errors'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { ChatRightRail } from '../right-rail/chat-right-rail'
import { ManagedChildTask } from '../right-rail/managed-child-task'

const api = vi.hoisted(() => ({
  listChildTasks: vi.fn(),
  getChildTask: vi.fn(),
  sendChildInstruction: vi.fn(),
  cancelChildTask: vi.fn(),
  waitForChildTask: vi.fn(),
}))
vi.mock('@/lib/api/child-tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/child-tasks')>()),
  ...api,
}))

const conversationId = 'e531ef30-946e-4c39-a80f-a581e26ecb67'
const researcher = {
  id: 'd4e92ff8-f91b-4690-926b-c6672d24c02e',
  tool_call_id: 'call-1',
  role: 'researcher',
  child_agent_id: '5bf48e39-0379-4be6-b730-7343292d7a6a',
  brief: null,
  run_id: '105e1b75-7719-4dc7-a3b8-fad7815891eb',
  status: 'running',
  checkpointed: true,
  created_at: '2026-11-27T01:00:00Z',
  updated_at: '2026-11-27T01:01:00Z',
  result: null,
  instructions: [],
}
const reviewer = {
  ...researcher,
  id: '6a5120bf-5518-4350-82c4-6e8251ee1fa2',
  tool_call_id: 'call-2',
  role: 'reviewer',
  child_agent_id: '0b68c2fc-6437-4a88-9917-f3059c9e57f0',
}

function serveTasks<T extends { readonly id: string }>(items: readonly T[]) {
  api.listChildTasks.mockResolvedValue({
    items,
    enabled: true,
    allow_legacy_details: false,
    next_offset: null,
  })
  api.getChildTask.mockImplementation(async (_conversationId, taskId) => {
    const task = items.find((item) => item.id === taskId)
    if (!task) throw new ApiError(404, 'not_found', 'Not found')
    return task
  })
}

function renderContent(children: ReactNode) {
  const queryClient = createTestQueryClient()
  const view = render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </NextIntlClientProvider>,
  )
  return { ...view, queryClient }
}

function renderTasks(...ids: string[]) {
  return renderContent(
    ids.map((id) => <ManagedChildTask key={id} conversationId={conversationId} taskId={id} />),
  )
}

function RestoredThread({ children }: { readonly children: ReactNode }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages: [],
    onNew: async () => undefined,
  })
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
}

function renderRestoredStrip(selectedTaskId?: string) {
  const store = createStore()
  renderContent(
    <JotaiProvider store={store}>
      <RestoredThread>
        <ChatConversationContext.Provider value={conversationId}>
          <DelegatedChildTasks conversationId={conversationId} />
          {selectedTaskId && (
            <ManagedChildTask conversationId={conversationId} taskId={selectedTaskId} />
          )}
        </ChatConversationContext.Provider>
      </RestoredThread>
    </JotaiProvider>,
  )
  return store
}

describe('managed child task controls', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    serveTasks([researcher, reviewer])
  })

  it('opens the persisted child by its durable id after reconnecting with no live stream', async () => {
    const user = userEvent.setup()
    const store = renderRestoredStrip()
    await user.click(await screen.findByRole('button', { name: 'Open reviewer details' }))

    expect(store.get(chatRightRailAtom)).toMatchObject({
      mode: 'subagent',
      subagent: { conversationId, childTaskId: reviewer.id, toolCallId: reviewer.tool_call_id },
    })
    expect(api.sendChildInstruction).not.toHaveBeenCalled()
  })

  it('distinguishes parallel tasks with the same role in both the strip and the selected panel', async () => {
    const user = userEvent.setup()
    serveTasks([researcher, { ...reviewer, role: researcher.role }])
    const store = renderRestoredStrip(reviewer.id)
    const firstLabel = `researcher · ${researcher.id.slice(0, 8)}`
    const secondLabel = `researcher · ${reviewer.id.slice(0, 8)}`

    expect(await screen.findByRole('button', { name: `Open ${firstLabel} details` })).toBeVisible()
    await user.click(screen.getByRole('button', { name: `Open ${secondLabel} details` }))
    const selectedPanel = within(screen.getByRole('region', { name: secondLabel }))
    expect(selectedPanel.getByRole('heading', { name: secondLabel })).toBeVisible()
    expect(store.get(chatRightRailAtom)).toMatchObject({
      mode: 'subagent',
      subagent: { childTaskId: reviewer.id },
    })
  })

  it('hides cached content and controls when the server revokes access to the conversation', async () => {
    serveTasks([{ ...researcher, result: 'Restricted interim report' }])
    const store = createStore()
    // The persisted rail selection can outlive access to the selected child.
    store.set(chatRightRailAtom, {
      mode: 'subagent',
      subagent: {
        conversationId,
        toolCallId: researcher.tool_call_id,
        childTaskId: researcher.id,
        agentName: 'Previously cached confidential role',
      },
    })
    const { queryClient } = renderContent(
      <JotaiProvider store={store}>
        <ChatRightRail conversationId={conversationId} />
      </JotaiProvider>,
    )
    expect((await screen.findAllByText('Restricted interim report')).length).toBeGreaterThan(0)

    const denial = new ApiError(404, 'not_found', 'Not found')
    api.listChildTasks.mockRejectedValue(denial)
    api.getChildTask.mockRejectedValue(denial)
    await act(async () => {
      await queryClient.invalidateQueries()
    })

    expect((await screen.findAllByText('This task is unavailable.')).length).toBeGreaterThan(0)
    expect(screen.queryByText('Previously cached confidential role')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: researcher.role })).not.toBeInTheDocument()
    expect(screen.queryByText('Restricted interim report')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('hides a selected completed task on a newly empty full list even while its detail cache survives', async () => {
    const receipt = {
      id: 'ecb74af0-6a05-4158-b8de-95dd78a403a2',
      client_request_id: '58ed908d-bcc7-4abb-960c-8b214b9691e9',
      text: 'Restricted follow-up',
      status: 'applied',
      created_at: '2026-11-27T01:01:03Z',
      applied_at: '2026-11-27T01:01:10Z',
    }
    const completed = {
      ...researcher,
      status: 'completed',
      brief: 'Restricted brief',
      result: 'Restricted final result',
      instructions: [receipt],
    }
    serveTasks([completed])
    const page = { enabled: false, allow_legacy_details: false, next_offset: null }
    api.listChildTasks.mockResolvedValue({ ...page, items: [completed] })
    const { queryClient } = renderTasks(completed.id)
    expect(await screen.findByText(completed.brief)).toBeVisible()
    expect(screen.getByText(receipt.text)).toBeVisible()

    // Authorization has removed this child from the new, complete list.
    // A subsequent individual read cannot complete before the old details disappear.
    api.listChildTasks.mockResolvedValue({ ...page, items: [] })
    api.getChildTask.mockImplementation(() => new Promise(() => undefined))
    await act(async () => {
      await queryClient.invalidateQueries({
        queryKey: conversationQueryKeys.childTasks(conversationId, currentOrgScope().generation),
        exact: true,
      })
    })

    await waitFor(() =>
      expect(screen.queryByRole('region', { name: researcher.role })).not.toBeInTheDocument(),
    )
    expect(screen.queryByText(completed.brief)).not.toBeInTheDocument()
    expect(screen.queryByText(completed.result)).not.toBeInTheDocument()
    expect(screen.queryByText(receipt.text)).not.toBeInTheDocument()
  })

  it('requires a fresh exact read before displaying a selected task that moved beyond loaded pages', async () => {
    const initial = { ...reviewer, status: 'completed', brief: 'Old brief', result: 'Old result' }
    serveTasks([initial])
    const { queryClient } = renderTasks(reviewer.id)
    expect(await screen.findByText(initial.brief)).toBeVisible()

    api.listChildTasks.mockResolvedValue({
      items: [researcher],
      enabled: true,
      allow_legacy_details: false,
      next_offset: 50,
    })
    const read = Promise.withResolvers<typeof initial>()
    api.getChildTask.mockClear().mockReturnValue(read.promise)
    await act(async () => {
      await queryClient.invalidateQueries({
        queryKey: conversationQueryKeys.childTasks(conversationId, currentOrgScope().generation),
        exact: true,
      })
    })

    await waitFor(() =>
      expect(api.getChildTask).toHaveBeenCalledWith(
        conversationId,
        reviewer.id,
        expect.any(AbortSignal),
      ),
    )
    expect(screen.queryByText(initial.brief)).not.toBeInTheDocument()
    expect(screen.queryByText(initial.result)).not.toBeInTheDocument()
    const refreshed = { ...initial, brief: 'Verified brief', result: 'Verified result' }
    await act(async () => {
      read.resolve(refreshed)
      await read.promise
    })
    expect(await screen.findByText(refreshed.brief)).toBeVisible()
    expect(screen.getByText(refreshed.result)).toBeVisible()
  })

  it('recovers a selected task after a temporary read failure without issuing a command', async () => {
    const user = userEvent.setup()
    // Make only the selected resource unavailable. Its id is older than the loaded page.
    api.listChildTasks.mockResolvedValue({
      items: [researcher],
      enabled: true,
      allow_legacy_details: false,
      next_offset: 20,
    })
    api.getChildTask.mockRejectedValue(new ApiError(503, 'temporarily_unavailable', 'Unavailable'))
    renderTasks(reviewer.id)

    const retry = await screen.findByRole(
      'button',
      { name: /retry|try again/i },
      { timeout: 15000 },
    )
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    api.getChildTask.mockResolvedValue({ ...reviewer, brief: 'Recovered task' })
    await user.click(retry)

    expect(await screen.findByText('Recovered task')).toBeVisible()
    expect(screen.getByRole('textbox', { name: /reviewer/i })).toBeEnabled()
    expect(api.sendChildInstruction).not.toHaveBeenCalled()
    expect(api.cancelChildTask).not.toHaveBeenCalled()
  }, 25000)

  it('reuses the request id after a failed send and updates only the chosen sibling', async () => {
    const user = userEvent.setup()
    const instruction = 'Only compare public agencies'
    api.sendChildInstruction.mockRejectedValueOnce(new Error('Disconnected'))
    api.sendChildInstruction.mockImplementationOnce(async (_conversation, _child, input) => {
      const receipt = {
        id: '482ddd42-59c6-433a-b5a3-133c7c60d903',
        client_request_id: input.client_request_id,
        text: input.text,
        status: 'pending',
        created_at: '2026-11-27T01:02:00Z',
        applied_at: null,
      }
      const updated = { ...researcher, instructions: [receipt] }
      serveTasks([updated, reviewer])
      return updated
    })
    renderTasks(researcher.id, reviewer.id)
    const first = within(await screen.findByRole('region', { name: researcher.role }))
    const sibling = within(screen.getByRole('region', { name: reviewer.role }))
    const draft = first.getByRole('textbox', { name: /send an instruction to researcher/i })
    const send = first.getByRole('button', { name: /send instruction/i })

    await user.type(draft, instruction)
    await user.click(send)
    await waitFor(() => expect(api.sendChildInstruction).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(send).toBeEnabled())
    expect(draft).toHaveValue(instruction)
    expect(sibling.getByRole('textbox')).toHaveValue('')

    await user.click(send)
    await waitFor(() => expect(api.sendChildInstruction).toHaveBeenCalledTimes(2))
    const request = { client_request_id: expect.any(String), text: instruction }
    expect(api.sendChildInstruction.mock.calls[0]).toEqual([conversationId, researcher.id, request])
    expect(api.sendChildInstruction.mock.calls[1]).toEqual(api.sendChildInstruction.mock.calls[0])
    await waitFor(() => expect(draft).toHaveValue(''))
    expect(await first.findByText(instruction)).toBeInTheDocument()
    expect(sibling.queryByText(instruction)).not.toBeInTheDocument()
  })

  it('restores a selected task beyond the first page by its own id', async () => {
    const historical = {
      ...reviewer,
      status: 'completed',
      result: 'Historical review',
      brief: 'Audit the figures.',
    }
    serveTasks([researcher, historical])
    api.listChildTasks.mockResolvedValue({
      items: [researcher],
      enabled: true,
      allow_legacy_details: false,
      next_offset: 20,
    })
    renderTasks(reviewer.id)

    expect(await screen.findByText(historical.result)).toBeVisible()
    expect(screen.getByText(historical.brief)).toBeVisible()
    expect(
      api.getChildTask.mock.calls.some(
        ([owner, child]) => owner === conversationId && child === reviewer.id,
      ),
    ).toBe(true)
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('restores a finished result and its applied instruction without offering live controls', async () => {
    const completed = {
      ...researcher,
      status: 'completed',
      result: 'Three agencies checked.',
      brief: 'Compare the municipal housing agencies.',
      instructions: [
        {
          id: '28268f13-db67-4698-ae79-923cb62b7149',
          client_request_id: '03093257-bc88-4775-9692-2871d2dadc1e',
          text: 'Check exactly three',
          status: 'applied',
          created_at: '2026-11-27T01:01:03Z',
          applied_at: '2026-11-27T01:01:08Z',
        },
      ],
    }
    serveTasks([completed, reviewer])
    renderTasks(researcher.id)

    expect(await screen.findByText(completed.result)).toBeInTheDocument()
    expect(screen.getByText(completed.brief)).toBeVisible()
    expect(screen.getByText(completed.instructions[0].text)).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /stop task/i })).not.toBeInTheDocument()
    expect(api.sendChildInstruction).not.toHaveBeenCalled()
  })

  it('still permits waiting and stopping a captured task after new instructions are disabled', async () => {
    api.listChildTasks.mockResolvedValue({
      items: [reviewer],
      next_offset: null,
      enabled: false,
      allow_legacy_details: false,
    })
    renderTasks(reviewer.id)

    const panel = within(await screen.findByRole('region', { name: reviewer.role }))
    expect(panel.queryByRole('textbox')).not.toBeInTheDocument()
    expect(panel.queryByRole('button', { name: /send instruction/i })).not.toBeInTheDocument()
    expect(panel.getByRole('button', { name: /wait for result/i })).toBeEnabled()
    expect(panel.getByRole('button', { name: /stop task/i })).toBeEnabled()
  })

  it('keeps an approval-waiting child available after a read-only wait and stops only that child', async () => {
    const user = userEvent.setup()
    const approval = { ...reviewer, status: 'awaiting_approval' }
    serveTasks([researcher, approval])
    api.waitForChildTask.mockResolvedValue(approval)
    api.cancelChildTask.mockImplementation(async () => {
      const cancelled = { ...approval, status: 'cancelled' }
      serveTasks([researcher, cancelled])
      return cancelled
    })
    renderTasks(reviewer.id, researcher.id)
    const child = within(await screen.findByRole('region', { name: reviewer.role }))
    const sibling = within(screen.getByRole('region', { name: researcher.role }))
    await user.click(child.getByRole('button', { name: /wait for result/i }))
    await waitFor(() => expect(api.waitForChildTask).toHaveBeenCalledTimes(1))
    expect(api.waitForChildTask.mock.calls[0].slice(0, 2)).toEqual([conversationId, reviewer.id])
    expect(api.cancelChildTask).not.toHaveBeenCalled()
    expect(child.getByRole('textbox')).toBeEnabled()
    await user.click(child.getByRole('button', { name: /stop task/i }))

    await waitFor(() =>
      expect(api.cancelChildTask).toHaveBeenCalledWith(conversationId, reviewer.id),
    )
    await waitFor(() => expect(child.queryByRole('textbox')).not.toBeInTheDocument())
    expect(sibling.getByRole('textbox')).toBeEnabled()
  })
})
