import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  message,
  queuedInput,
  queueList,
} from '@/lib/chat/message-queue/__tests__/server-message-queue-test-fixtures'
import { useAgUiChatActions } from '../chat-actions'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  cancel: vi.fn(),
}))
vi.mock('@/lib/api/conversation-run-inputs', () => ({
  conversationRunInputsApi: { list: mocks.list },
}))
vi.mock('@/lib/hooks/use-conversation-runs', () => ({
  useCancelConversationRun: () => ({ mutateAsync: mocks.cancel }),
}))

const agent = { readSnapshot: vi.fn(), submitQueuedInput: vi.fn(), retryFailedInput: vi.fn() }
const options = {
  agent,
  conversationId: 'conversation-1',
  activeRunId: null as string | null,
  activePhysicalRunId: null as string | null,
  onClaimedRun: vi.fn(),
}
const file = 'b9230676-eb20-42df-8783-b660570fc34a'
const resource = { kind: 'file', id: file } as const

describe('AG-UI chat actions', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mocks.list.mockResolvedValue(queueList([]))
  })

  it('enqueues the current user text, stored attachment and selected resources with one durable identity', async () => {
    const item = queuedInput()
    mocks.list.mockResolvedValue(queueList([item]))
    agent.submitQueuedInput.mockResolvedValue({
      inputId: item.id,
      inputStatus: 'pending',
      revision: 1,
      position: 1,
    })
    const { result } = renderHook(() => useAgUiChatActions(options))
    await waitFor(() => expect(result.current.actions.shouldQueue).toBe(true))
    const draft = {
      ...message('queued', [file]),
      runConfig: { custom: { resource_context: [resource] } },
    }
    await act(() => result.current.actions.queueDraft(draft, false))
    const [payload, strategy, requestId] = agent.submitQueuedInput.mock.calls[0]
    expect(payload).toEqual({
      id: requestId,
      text: 'queued',
      attachmentIds: [file],
      resourceContext: { resources: [resource] },
    })
    expect(strategy).toBe('enqueue')
    expect(agent.submitQueuedInput).toHaveBeenCalledTimes(1)
    expect(mocks.cancel).not.toHaveBeenCalled()
  })

  it('blocks another submission while its durable receipt is being reconciled', async () => {
    agent.submitQueuedInput.mockRejectedValue(new TypeError('receipt lost'))
    mocks.list.mockRejectedValue(new Error('read offline'))
    const { result } = renderHook(() => useAgUiChatActions(options))
    const draft = message('do this once')
    let accepted!: Promise<void>
    await act(async () => {
      accepted = result.current.actions.queueDraft(draft, true)
    })
    expect(result.current.messageQueue.getSnapshot().rejectedSubmission?.message).toBe(draft)
    await expect(result.current.actions.queueDraft(draft, true)).rejects.toThrow()
    const requestId = agent.submitQueuedInput.mock.calls[0][2]
    mocks.list.mockResolvedValue(queueList([queuedInput({ client_request_id: requestId })]))
    await act(() => result.current.messageQueue.refresh())
    await accepted
    expect(agent.submitQueuedInput).toHaveBeenCalledTimes(1)
    expect(agent.submitQueuedInput.mock.calls[0][1]).toBe('interrupt')
    expect(mocks.cancel).not.toHaveBeenCalled()
  })

  it('preserves the same command ID on an explicit retry of an unchanged rejected draft', async () => {
    const { result } = renderHook(() => useAgUiChatActions(options))
    const draft = message('keep my draft')
    agent.submitQueuedInput.mockRejectedValueOnce(new TypeError('receipt lost'))
    await act(async () => {
      await expect(result.current.actions.queueDraft(draft, false)).rejects.toThrow()
    })
    agent.submitQueuedInput.mockResolvedValueOnce({
      inputId: 'accepted',
      inputStatus: 'pending',
      revision: 1,
      position: 1,
    })
    await act(() => result.current.actions.queueDraft(draft, false))
    expect(agent.submitQueuedInput.mock.calls[1][2]).toBe(agent.submitQueuedInput.mock.calls[0][2])
  })

  it.each(['edit', 'regenerate'] as const)(
    'loads the selected message parent and active head for %s',
    async (operation) => {
      const { result } = renderHook(() => useAgUiChatActions(options))
      agent.readSnapshot.mockResolvedValue({
        messageLinks: [
          { messageId: 'wire', uiMessageId: file, parentCheckpointId: 'parent-checkpoint' },
        ],
        checkpointId: 'current-leaf',
      })
      const selected = { id: 'local-id', metadata: { custom: { agui: { messageId: 'wire' } } } }
      const config = await result.current.actions.getBranchRunConfig(operation, selected, {
        custom: { resource_context: [resource] },
      })
      expect(config.custom).toEqual({
        resource_context: [resource],
        moldy: { operation, checkpointId: 'parent-checkpoint', expectedHead: 'current-leaf' },
      })
      await expect(
        result.current.actions.getBranchRunConfig(operation, {
          id: file,
          metadata: { custom: { agui: { messageId: 'different-branch' } } },
        }),
      ).rejects.toThrow()
      expect(agent.submitQueuedInput).not.toHaveBeenCalled()
    },
  )

  it('cancels only the explicitly displayed physical run and deduplicates a double click', async () => {
    let complete!: () => void
    mocks.cancel.mockReturnValue(
      new Promise<void>((resolve) => {
        complete = resolve
      }),
    )
    const { result, unmount } = renderHook(() =>
      useAgUiChatActions({ ...options, activeRunId: 'public', activePhysicalRunId: 'physical' }),
    )
    expect(mocks.cancel).not.toHaveBeenCalled()
    const first = result.current.actions.stopActiveRun()
    const second = result.current.actions.stopActiveRun()
    expect(mocks.cancel).toHaveBeenCalledExactlyOnceWith('physical')
    complete()
    await act(() => Promise.all([first, second]).then(() => undefined))
    unmount()
    expect(mocks.cancel).toHaveBeenCalledTimes(1)
  })

  it('does not stop a successor or a run in a switched conversation while awaiting GET', async () => {
    const { result, rerender } = renderHook((props) => useAgUiChatActions(props), {
      initialProps: { ...options, activeRunId: 'public' },
    })
    agent.readSnapshot.mockResolvedValueOnce({
      activeRunId: 'successor',
      physicalRunId: 'runs-later',
    })
    await result.current.actions.stopActiveRun()
    let resolve!: (value: unknown) => void
    agent.readSnapshot.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      }),
    )
    const pending = result.current.actions.stopActiveRun()
    rerender({ ...options, conversationId: 'conversation-2', activeRunId: 'next' })
    resolve({ activeRunId: 'public', physicalRunId: 'old-physical' })
    await pending
    expect(mocks.cancel).not.toHaveBeenCalled()
  })

  it('reuses an uncertain failed-input retry ID and follows its claimed row just once', async () => {
    const input = queuedInput({ status: 'failed', run_id: 'failed-run' })
    const { result } = renderHook(() => useAgUiChatActions(options))
    agent.retryFailedInput.mockRejectedValueOnce(new TypeError('response lost'))
    mocks.list.mockRejectedValueOnce(new Error('reconciliation offline'))
    await act(async () => {
      await expect(result.current.retryFailedInput(input)).rejects.toThrow()
    })
    const request = agent.retryFailedInput.mock.calls[0][1]
    const claimed = queuedInput({
      run_id: 'retry-run',
      status: 'claimed',
      client_request_id: request,
      claimed_at: '2026-11-26T01:00:00Z',
    })
    agent.retryFailedInput.mockResolvedValueOnce({
      inputId: claimed.id,
      inputStatus: 'claimed',
      runId: 'retry-run',
      position: 1,
      revision: 1,
    })
    mocks.list.mockResolvedValue(queueList([claimed]))
    await act(() => result.current.retryFailedInput(input))
    expect(agent.retryFailedInput.mock.calls).toEqual([
      [input.id, request],
      [input.id, request],
    ])
    expect(options.onClaimedRun).toHaveBeenCalledExactlyOnceWith('retry-run')
  })
})
