import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  useExternalStoreRuntime,
  type ThreadMessage,
} from '@assistant-ui/react'
import { act } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor, within } from '../../../../tests/test-utils'
import { conversationRunInputsApi } from '@/lib/api/conversation-run-inputs'
import { createServerMessageQueue } from '@/lib/chat/message-queue/server-message-queue'
import { ServerMessageQueueProvider } from '@/lib/chat/message-queue/server-message-queue-context'
import { queuedInput } from '@/lib/chat/message-queue/__tests__/server-message-queue-test-fixtures'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { conversationRunSteersApi, type RunSteerReceipt } from '@/lib/api/conversation-run-steers'
import type { ConversationRun } from '@/lib/types'
import { RunSteerHistory, RunSteerSend } from '../run-steer-controls'

vi.mock('@/lib/api/conversation-runs')
vi.mock('@/lib/api/conversation-run-steers')

const conversationId = 'conversation-A'
const created = '2026-11-01T00:00:00Z'
const activeRun: ConversationRun = {
  id: 'run-A',
  conversation_id: conversationId,
  agent_id: 'agent-A',
  parent_run_id: null,
  status: 'running',
  source: 'chat',
  worker_instance_id: null,
  interrupt_id: null,
  last_event_id: null,
  input_preview: null,
  error_code: null,
  error_message: null,
  cancel_requested_at: null,
  started_at: created,
  heartbeat_at: null,
  completed_at: null,
  created_at: created,
  updated_at: created,
  metrics: null,
}
const receipt: RunSteerReceipt = {
  id: 'steer-A',
  conversation_id: conversationId,
  run_id: activeRun.id,
  client_request_id: 'request-A',
  sequence: 1,
  text: 'Restrict to local vendors',
  status: 'pending',
  created_at: created,
  applied_at: null,
  queued_input_id: null,
}

const onCancel = vi.fn(async () => {})
const onNew = vi.fn(async () => {})
const queue = {
  items: [],
  steerItems: [],
  enqueue: vi.fn(),
  steer: vi.fn(),
  move: vi.fn(),
  edit: vi.fn(),
  remove: vi.fn(),
}

function Runtime({ children, running = true }: PropsWithChildren<{ running?: boolean }>) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages: [],
    isRunning: running,
    queue,
    onCancel,
    onNew,
  })
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
}

function renderSend(hasResourceContext = false) {
  return render(
    <Runtime>
      <ComposerPrimitive.Input />
      <RunSteerSend conversationId={conversationId} hasResourceContext={hasResourceContext} />
    </Runtime>,
  )
}

const steer = () => screen.getByRole('button', { name: '진행 중 작업에 보충' })

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(conversationRunsApi.active).mockResolvedValue(activeRun)
  vi.mocked(conversationRunSteersApi.list).mockResolvedValue([])
  vi.mocked(conversationRunSteersApi.submit).mockImplementation(async (id, request) => ({
    ...receipt,
    conversation_id: id,
    run_id: request.runId,
    client_request_id: request.requestId,
    text: request.text,
  }))
})

describe('same-run instructions', () => {
  it('retries an uncertain response against its original run and request ID', async () => {
    const user = userEvent.setup()
    vi.mocked(conversationRunSteersApi.submit).mockImplementationOnce(async () => {
      vi.mocked(conversationRunsApi.active).mockResolvedValue({ ...activeRun, id: 'new-run' })
      throw new TypeError('lost response')
    })
    renderSend()
    const draft = screen.getByRole('textbox')
    await user.type(draft, receipt.text)
    await user.click(steer())
    await user.click(await screen.findByRole('button', { name: '보충 지시 다시 보내기' }))
    await waitFor(() => expect(draft).toHaveValue(''))
    const attempts = vi.mocked(conversationRunSteersApi.submit).mock.calls
    expect(attempts).toHaveLength(2)
    expect(attempts[0]).toEqual([
      conversationId,
      { runId: activeRun.id, requestId: expect.any(String), text: receipt.text },
    ])
    expect(attempts[1]).toEqual(attempts[0])
    expect(queue.enqueue).not.toHaveBeenCalled()
    expect(queue.steer).not.toHaveBeenCalled()
    expect(onNew).not.toHaveBeenCalled()
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('reconciles a lost acknowledgment against the server ledger before offering a retry', async () => {
    const user = userEvent.setup()
    vi.mocked(conversationRunSteersApi.submit).mockImplementationOnce(async (_, request) => {
      vi.mocked(conversationRunSteersApi.list).mockResolvedValue([
        { ...receipt, client_request_id: request.requestId, run_id: request.runId },
      ])
      throw new TypeError('lost acknowledgment')
    })
    renderSend()
    const draft = screen.getByRole('textbox')
    await user.type(draft, receipt.text)
    await user.click(steer())
    await waitFor(() => expect(draft).toHaveValue(''))
    expect(conversationRunSteersApi.submit).toHaveBeenCalledOnce()
    expect(conversationRunSteersApi.list).toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '보충 지시 다시 보내기' })).toBeNull()
  })

  it('keeps the newer draft when the receipt for the prior draft arrives', async () => {
    const user = userEvent.setup()
    let accept: (row: RunSteerReceipt) => void = () => {
      throw Error('No pending submission')
    }
    vi.mocked(conversationRunSteersApi.submit).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          accept = resolve
        }),
    )
    renderSend()
    const draft = screen.getByRole('textbox')
    await user.type(draft, receipt.text)
    await user.click(steer())
    expect(steer()).toBeDisabled()
    await user.clear(draft)
    await user.type(draft, 'another instruction')
    await act(async () => accept(receipt))
    await waitFor(() => expect(steer()).toBeEnabled())
    expect(draft).toHaveValue('another instruction')
  })

  it('preserves a draft containing resource references for another action', async () => {
    const user = userEvent.setup()
    renderSend(true)
    const draft = screen.getByRole('textbox')
    await user.type(draft, 'Explain this reference')
    expect(steer()).toBeDisabled()
    await user.click(steer())
    expect(conversationRunSteersApi.submit).not.toHaveBeenCalled()
    expect(draft).toHaveValue('Explain this reference')
  })

  it('disables submission while the server reports an outstanding interrupt', async () => {
    const user = userEvent.setup()
    vi.mocked(conversationRunsApi.active).mockResolvedValue({
      ...activeRun,
      status: 'interrupted',
      interrupt_id: 'interrupt-A',
    })
    renderSend()
    await user.type(screen.getByRole('textbox'), 'do next')
    expect(steer()).toBeDisabled()
    expect(conversationRunSteersApi.submit).not.toHaveBeenCalled()
  })

  it('sends to a running resume even when it retains the resolved interrupt ID', async () => {
    const user = userEvent.setup()
    vi.mocked(conversationRunsApi.active).mockResolvedValue({
      ...activeRun,
      id: 'resumed-run',
      parent_run_id: activeRun.id,
      source: 'resume',
      interrupt_id: 'interrupt-A',
    })
    renderSend()
    const draft = screen.getByRole('textbox')
    await user.type(draft, receipt.text)
    await waitFor(() => expect(steer()).toBeEnabled())
    await user.click(steer())
    expect(conversationRunSteersApi.submit).toHaveBeenCalledWith(conversationId, {
      runId: 'resumed-run',
      requestId: expect.any(String),
      text: receipt.text,
    })
    await waitFor(() => expect(draft).toHaveValue(''))
  })

  it('keeps checking a pending instruction after the run has finished', async () => {
    const rolled = queuedInput({ id: 'queue-A', conversation_id: conversationId })
    const controller = createServerMessageQueue({
      conversationId,
      api: {
        ...conversationRunInputsApi,
        list: vi.fn(async () => ({ items: [rolled], queue_paused: false })),
      },
      submit: vi.fn(),
      createRequestId: vi.fn(),
      onClaimedRun: vi.fn(),
    })
    const refresh = vi.spyOn(controller, 'refresh')
    vi.mocked(conversationRunSteersApi.list)
      .mockResolvedValueOnce([receipt])
      .mockResolvedValue([{ ...receipt, status: 'queued', queued_input_id: rolled.id }])
    render(
      <Runtime running={false}>
        <ServerMessageQueueProvider controller={controller}>
          <RunSteerHistory conversationId={conversationId} />
        </ServerMessageQueueProvider>
      </Runtime>,
    )
    expect(await screen.findByText('접수됨')).toBeVisible()
    expect(refresh).not.toHaveBeenCalled()
    expect(controller.getSnapshot().items).toHaveLength(0)
    expect(await screen.findByText('다음 요청으로 이월', {}, { timeout: 5000 })).toBeVisible()
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce())
    expect(controller.getSnapshot().items).toEqual([rolled])
    expect(screen.queryByText('접수됨')).toBeNull()
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('opens older receipts on demand and returns to the latest page', async () => {
    const user = userEvent.setup()
    const makeReceipt = (sequence: number): RunSteerReceipt => ({
      ...receipt,
      id: `steer-${sequence}`,
      client_request_id: `request-${sequence}`,
      sequence,
      text: `Instruction ${sequence}`,
      status: 'applied',
      applied_at: '2026-11-01T00:05:00Z',
    })
    const recent = Array.from({ length: 50 }, (_, index) => makeReceipt(index + 101))
    const unresolved: RunSteerReceipt = {
      ...makeReceipt(100),
      status: 'needs_review',
      applied_at: null,
    }
    vi.mocked(conversationRunSteersApi.list).mockResolvedValue(recent)
    vi.mocked(conversationRunSteersApi.listBefore).mockResolvedValue([unresolved, unresolved])
    render(
      <Runtime running={false}>
        <RunSteerHistory conversationId={conversationId} />
      </Runtime>,
    )
    expect(await screen.findByText('Instruction 150')).toBeVisible()
    expect(screen.getAllByRole('listitem')).toHaveLength(50)
    expect(conversationRunSteersApi.listBefore).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '이전 지시 보기' }))
    const older = await screen.findByText('Instruction 100')
    expect(conversationRunSteersApi.listBefore).toHaveBeenCalledWith(
      conversationId,
      101,
      expect.any(AbortSignal),
    )
    expect(older.closest('li')).toHaveTextContent('반영 여부 확인 필요')
    expect(screen.getAllByText('Instruction 100')).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: '최근 지시 보기' }))
    expect(await screen.findByText('Instruction 150')).toBeVisible()
    expect(screen.queryByText('Instruction 100')).toBeNull()
    expect(screen.getAllByRole('listitem')).toHaveLength(50)
  })

  it('restores persisted statuses for completed runs and opens criteria review only on request', async () => {
    const user = userEvent.setup()
    const review = vi.fn<(event: Event) => void>()
    window.addEventListener('moldy:review-steer-criterion', review)
    const history = (goalId: string) => (
      <Runtime running={false}>
        <section id="conversation-goal" data-steer-conversation={goalId} />
        <RunSteerHistory conversationId={conversationId} />
      </Runtime>
    )
    try {
      vi.mocked(conversationRunSteersApi.list).mockResolvedValue([receipt])
      const first = render(history('other-conversation'))
      expect(await screen.findByText('접수됨')).toBeVisible()
      expect(screen.queryByRole('button', { name: '완료 조건에도 반영' })).toBeNull()
      first.unmount()

      vi.mocked(conversationRunSteersApi.list).mockResolvedValue([
        { ...receipt, status: 'applied', applied_at: '2026-11-01T00:05:00Z' },
        {
          ...receipt,
          id: 'steer-B',
          text: 'Add a chart',
          status: 'queued',
          queued_input_id: 'queue-B',
        },
        { ...receipt, id: 'steer-C', text: 'Check the result', status: 'needs_review' },
      ])
      render(history(conversationId))
      const applied = await screen.findByText('반영됨')
      const appliedItem = applied.closest('li')
      if (!appliedItem) throw Error('Expected receipt row')
      expect(appliedItem).toHaveTextContent(receipt.text)
      expect(screen.getByText('다음 요청으로 이월').closest('li')).toHaveTextContent('Add a chart')
      expect(screen.getByText('반영 여부 확인 필요').closest('li')).toHaveTextContent(
        'Check the result',
      )
      expect(review).not.toHaveBeenCalled()
      await user.click(within(appliedItem).getByRole('button', { name: '완료 조건에도 반영' }))
      expect(review).toHaveBeenCalledOnce()
      expect(review.mock.calls[0][0]).toMatchObject({
        detail: { conversationId, text: receipt.text },
      })
      expect(conversationRunSteersApi.submit).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('moldy:review-steer-criterion', review)
    }
  })
})
