import { EventType } from '@ag-ui/core'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '../../../../tests/test-utils'
import { createAgUiChatSession, type AgUiChatSessionState } from '@/lib/ag-ui/chat-history'
import { parseWireChatSnapshot } from '@/lib/ag-ui/chat-history-types'
import { useCanceledRunSummary } from '@/lib/ag-ui/use-canceled-run-summary'
import type { ConversationRun } from '@/lib/types'
import { ChatConversationContext } from '../conversation-context'
import { CanceledRunSummary, CanceledRunSummaryContext, MessageRunSummary } from '../run-summary'

const aui = vi.hoisted(() => ({
  message: { id: 'late-answer' },
  thread: { isRunning: false, messages: [{ id: 'late-answer' }] },
}))
vi.mock('@assistant-ui/react', async (original) => ({
  ...(await original<typeof import('@assistant-ui/react')>()),
  useAuiState: (select: (state: typeof aui) => unknown) => select(aui),
}))

const threadId = '9a28c114-5ba9-4208-b511-a191e59ee1f4'
const physicalId = '3d32c52f-8020-4e32-a0ba-dd86e469a46a'
const olderId = 'a4eebcb3-9b75-4f08-8fd1-7e8792463823'
const runId = 'public-run'
const detailPath = `/api/conversations/${threadId}/runs/${physicalId}`
const linkPath = `/api/conversations/${threadId}/run-message-links?message_id=late-answer`
const timestamp = '2026-11-27T00:10:00Z'
const canceledRun: ConversationRun = {
  id: physicalId,
  conversation_id: threadId,
  agent_id: 'agent',
  parent_run_id: null,
  status: 'canceled',
  source: 'chat',
  worker_instance_id: null,
  interrupt_id: null,
  last_event_id: null,
  input_preview: null,
  error_code: null,
  error_message: null,
  cancel_requested_at: timestamp,
  started_at: timestamp,
  heartbeat_at: null,
  completed_at: timestamp,
  created_at: timestamp,
  updated_at: timestamp,
  metrics: {
    terminal_state: 'canceled',
    elapsed_ms: 700,
    ttft_ms: null,
    generation_ms: null,
    tokens_per_second: null,
    prompt_tokens: null,
    completion_tokens: null,
    cache_creation_tokens: null,
    cache_read_tokens: null,
    estimated_cost: null,
    usage_complete: false,
    root_tool_calls: 0,
    descendant_tool_calls: 0,
    root_subagent_calls: 0,
    descendant_subagent_calls: 0,
    activity_json: [],
    activity_truncated: false,
  },
}

function observeHttp(detailStatus = 200) {
  const requests: string[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
    const path = url.pathname + url.search
    requests.push(`${init?.method ?? 'GET'} ${path}`)
    if (path === detailPath) {
      return Response.json(detailStatus === 200 ? canceledRun : { detail: 'Forbidden' }, {
        status: detailStatus,
      })
    }
    if (path === linkPath) return Response.json([{ message_id: 'late-answer', run_id: physicalId }])
    throw new Error(`Unexpected request to ${path}`)
  })
  return requests
}

function sessionFor(fields: Record<string, unknown> = {}) {
  return createAgUiChatSession({
    conversationId: threadId,
    getServerMessages: () => [],
    readSnapshot: async () =>
      parseWireChatSnapshot(
        {
          threadId,
          protocolVersion: '1.0',
          projectionVersion: '1',
          checkpointId: null,
          activeTipMessageId: null,
          activeRunId: null,
          physicalRunId: null,
          interruptRunId: null,
          physicalInterruptRunId: null,
          cursor: null,
          messages: [],
          messageLinks: [],
          interrupts: [],
          subagents: [],
          runId,
          selectedPhysicalRunId: physicalId,
          latestPhysicalRunId: physicalId,
          runStatus: 'canceled',
          ...fields,
        },
        threadId,
      ),
  })
}

function Summary({
  state,
  latest = null,
  assistant = false,
}: {
  state: AgUiChatSessionState
  latest?: ConversationRun | null
  assistant?: boolean
}) {
  const summary = useCanceledRunSummary(threadId, state, latest)
  return (
    <ChatConversationContext.Provider value={threadId}>
      <CanceledRunSummaryContext.Provider value={summary}>
        <CanceledRunSummary />
        {assistant && <MessageRunSummary />}
      </CanceledRunSummaryContext.Provider>
    </ChatConversationContext.Provider>
  )
}

afterEach(() => vi.restoreAllMocks())

describe('cancellation before the first AG-UI assistant frame', () => {
  it('reads the confirmed physical run and renders one summary when a late answer acquires the same link', async () => {
    const requests = observeHttp()
    const session = sessionFor()
    const context = { source: 'run' as const, runId }
    session.onRunAccepted({ runId, physicalRunId: physicalId })
    session.onPublicEvent({ type: EventType.RUN_STARTED, threadId, runId }, context)
    session.onPublicEvent(
      { type: EventType.RUN_FINISHED, threadId, runId, outcome: { type: 'cancelled' } },
      context,
    )
    const state = session.getSnapshot()
    const view = render(<Summary state={state} />)
    expect(await screen.findByTestId('run-summary')).toHaveAttribute('data-run-id', physicalId)
    expect(screen.getByTestId('run-summary')).toHaveTextContent('도구 총 0')
    expect(requests).toEqual([`GET ${detailPath}`])
    view.rerender(<Summary state={state} assistant />)
    await waitFor(() => expect(requests).toContain(`GET ${linkPath}`))
    // The linked answer renders the same persisted summary when its standalone host is removed.
    view.rerender(<Summary state={{ ...state, lastTerminalPhysicalRunId: null }} assistant />)
    expect(await screen.findByTestId('run-summary')).toHaveAttribute('data-run-id', physicalId)
    view.rerender(<Summary state={state} assistant />)
    await waitFor(() => expect(screen.getAllByTestId('run-summary')).toHaveLength(1))
    expect(new Set(requests)).toEqual(new Set([`GET ${detailPath}`, `GET ${linkPath}`]))
    session.dispose()
  })

  it('restores a canceled branch with no assistant messages using the selected physical run', async () => {
    const requests = observeHttp()
    const session = sessionFor()
    await session.history.load()
    render(<Summary state={session.getSnapshot()} latest={{ ...canceledRun, id: olderId }} />)
    expect(await screen.findByTestId('run-summary')).toHaveAttribute('data-run-id', physicalId)
    expect(requests).toEqual([`GET ${detailPath}`])
    session.dispose()
  })

  it.each([
    {
      label: 'a canceled run selected on a different branch',
      fields: { selectedPhysicalRunId: olderId },
    },
    {
      label: 'an active run after the canceled turn',
      fields: { activeRunId: 'new-public', physicalRunId: olderId },
    },
  ])('skips $label', async ({ fields }) => {
    const requests = observeHttp()
    const session = sessionFor(fields)
    await session.history.load()
    render(<Summary state={session.getSnapshot()} latest={{ ...canceledRun, id: olderId }} />)
    expect(screen.queryByTestId('run-summary')).not.toBeInTheDocument()
    expect(requests).toEqual([])
    session.dispose()
  })

  it('clears a previously listed run after the scoped detail request is denied', async () => {
    const requests = observeHttp(403)
    const session = sessionFor()
    await session.history.load()
    render(<Summary state={session.getSnapshot()} latest={canceledRun} />)
    await waitFor(() => expect(screen.queryByTestId('run-summary')).not.toBeInTheDocument())
    expect(requests).toEqual([`GET ${detailPath}`])
    session.dispose()
  })
})
