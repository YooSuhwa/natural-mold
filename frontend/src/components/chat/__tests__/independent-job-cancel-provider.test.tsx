import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react'
import { useMemo, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { IndependentChatJob } from '@/lib/types/independent-chat-job'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { useStreamCancelController } from '@/lib/chat/langgraph-runtime/use-stream-cancel-controller'
import type { ConversationRun, SSEEvent } from '@/lib/types'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import { IndependentJobCancelProvider } from '../independent-job-cancel-provider'

vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))
vi.mock('@/lib/api/conversation-runs', () => ({
  conversationRunsApi: { active: vi.fn(), cancel: vi.fn(), get: vi.fn() },
}))

const running: ConversationRun = {
  id: 'run',
  conversation_id: 'parent',
  agent_id: 'agent',
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
  started_at: null,
  heartbeat_at: null,
  completed_at: null,
  created_at: '',
  updated_at: '',
  metrics: null,
}
const linkedJob: IndependentChatJob = {
  id: 'job',
  parent_agent_id: 'agent',
  parent_conversation_id: 'parent',
  parent_run_id: 'run',
  child_agent_id: 'child',
  child_agent_name: 'researcher',
  child_conversation_id: 'child-chat',
  child_run_id: 'child-run',
  status: 'waiting_for_approval',
}

const setChatCancelInFlight = vi.fn()

function Stop({ id, stop }: { id: string; stop: () => Promise<void> }) {
  const reconciliation = useMemo(() => {
    const lifetime = { id }
    return {
      lifetime,
      lifetimeRef: { current: lifetime },
      setThreadRunNotice: vi.fn(),
      clearPendingEdit: vi.fn(),
      clearPendingReload: vi.fn(),
      cancelPostRunHydration: vi.fn(),
      getPendingEditAttemptId: () => null,
      getPendingReloadAttemptId: () => null,
    }
  }, [id])
  const cancel = useStreamCancelController({
    conversationId: id,
    reconciliation,
    stream: { stop },
    setChatCancelInFlight,
  })
  return <button onClick={() => void cancel()}>Stop</button>
}

function mount(jobs: IndependentChatJob[] = [linkedJob]) {
  const stop = vi.fn().mockResolvedValue(undefined)
  const client = new QueryClient()
  vi.mocked(conversationRunsApi.active).mockResolvedValue(running)
  vi.mocked(conversationRunsApi.cancel).mockResolvedValue({ ...running, status: 'canceled' })
  const tree = (id: string) => (
    <QueryClientProvider client={client}>
      <IndependentJobCancelProvider conversationId={id} jobs={jobs}>
        <Stop id={id} stop={stop} />
      </IndependentJobCancelProvider>
    </QueryClientProvider>
  )
  const view = render(tree('parent'))
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
  return { stop, navigate: () => view.rerender(tree('other')) }
}

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('cancel scope for an independent job', () => {
  it.each(['parentOnly', 'withChildren'])(
    'waits for %s before detaching or canceling',
    async (button) => {
      const { stop } = mount()
      const action = await screen.findByRole('button', { name: button })
      expect(stop).not.toHaveBeenCalled()
      expect(conversationRunsApi.cancel).not.toHaveBeenCalled()
      fireEvent.click(action)
      const args = button === 'withChildren' ? ['parent', 'run', true] : ['parent', 'run']
      await waitFor(() => expect(conversationRunsApi.cancel).toHaveBeenCalledWith(...args))
      expect(stop).toHaveBeenCalledTimes(1)
    },
  )
  it('passes the explicit cascade choice through the legacy assistant-ui runtime', async () => {
    const client = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>
        <IndependentJobCancelProvider conversationId="parent" jobs={[linkedJob]}>
          {children}
        </IndependentJobCancelProvider>
      </QueryClientProvider>
    )
    vi.mocked(conversationRunsApi.active).mockResolvedValue(running)
    vi.mocked(conversationRunsApi.cancel).mockResolvedValue({ ...running, status: 'canceling' })
    let release!: () => void
    const pending = new Promise<void>((resolve) => {
      release = resolve
    })
    const onAbort = vi.fn()
    let ready!: () => void
    const started = new Promise<void>((resolve) => {
      ready = resolve
    })
    async function* streamFn(
      _content: string,
      signal: AbortSignal,
      options?: { onRunId?: (runId: string) => void },
    ): AsyncGenerator<SSEEvent> {
      options?.onRunId?.(running.id)
      signal.addEventListener('abort', onAbort)
      yield { event: 'message_start', id: 'a', data: { id: 'a', role: 'assistant' } }
      ready()
      await pending
      yield { event: 'message_end', id: 'b', data: { content: '', usage: {} } }
    }
    const { result } = renderHook(
      () => useChatRuntime({ conversationId: 'parent', messages: [], streamFn }),
      { wrapper },
    )
    let send!: Promise<void>
    try {
      act(() => {
        send = result.current.sendMessage('question')
      })
      await started
      act(() => result.current.runtime.thread.cancelRun())
      const cascade = await screen.findByRole('button', { name: 'withChildren' })
      expect(onAbort).not.toHaveBeenCalled()
      expect(conversationRunsApi.cancel).not.toHaveBeenCalled()
      fireEvent.click(cascade)
      await waitFor(() =>
        expect(conversationRunsApi.cancel).toHaveBeenCalledWith('parent', 'run', true),
      )
      await waitFor(() => expect(onAbort).toHaveBeenCalledOnce())
    } finally {
      release()
      await act(async () => {
        await send
      })
    }
  })
  it('keeps the stream attached when the choice is dismissed', async () => {
    const { stop } = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'dismiss' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(stop).not.toHaveBeenCalled()
    expect(conversationRunsApi.cancel).not.toHaveBeenCalled()
  })
  it('dismisses a pending choice on navigation', async () => {
    const { navigate, stop } = mount()
    await screen.findByRole('alertdialog')
    navigate()
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(stop).not.toHaveBeenCalled()
    expect(conversationRunsApi.cancel).not.toHaveBeenCalled()
  })
  it('cannot stop the next run if the original finishes while the dialog is open', async () => {
    const { stop } = mount()
    const action = await screen.findByRole('button', { name: 'withChildren' })
    vi.mocked(conversationRunsApi.active).mockResolvedValue({ ...running, id: 'new' })
    fireEvent.click(action)
    await waitFor(() => expect(conversationRunsApi.active).toHaveBeenCalledTimes(2))
    expect(stop).not.toHaveBeenCalled()
    expect(conversationRunsApi.cancel).not.toHaveBeenCalled()
  })
  it('does not prompt or cancel jobs belonging to a previous run', async () => {
    const { stop } = mount([{ ...linkedJob, parent_run_id: 'previous' }])
    await waitFor(() => expect(conversationRunsApi.cancel).toHaveBeenCalledWith('parent', 'run'))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(stop).toHaveBeenCalledTimes(1)
  })
})
