import type { Interrupt } from '@ag-ui/core'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChildTask } from '@/lib/api/child-tasks'
import { createAgUiChatSession, type AgUiChatSessionState } from '@/lib/ag-ui/chat-history'
import { parseWireChatSnapshot, type WireChatSnapshot } from '@/lib/ag-ui/chat-history-types'
import { beginOrgSwitch, finishOrgSwitch } from '@/lib/org-scope/org-scope-signal'
import { HiTLContext } from '@/lib/chat/hitl-context'
import { AgUiChildPendingForms } from '../ag-ui-child-pending-forms'
import { ChatWorkCommandConversationContext } from '../conversation-context'

const wire = vi.hoisted(() => ({
  interrupts: vi.fn(),
  params: vi.fn(),
  list: vi.fn(),
  detail: vi.fn(),
}))
vi.mock('@assistant-ui/react-ag-ui', async (load) => ({
  ...(await load<typeof import('@assistant-ui/react-ag-ui')>()),
  useAgUiInterrupts: wire.interrupts,
}))
vi.mock('@/lib/hooks/use-child-tasks', async (load) => ({
  ...(await load<typeof import('@/lib/hooks/use-child-tasks')>()),
  useChildTasks: wire.list,
  useChildTask: wire.detail,
}))
vi.mock('next/navigation', () => ({ useParams: wire.params }))
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))

const conversationId = '40c19c43-7f41-4261-a45f-f37f38f82884'
const publicRun = 'public-paused'
const physicalRun = 'ef7258d7-e4de-40ed-a3a9-8de35bc373ca'
const childRun = 'child-run'
const publicParentCall = 'tool-757c38eb-b07a-4ee2-90c7-0db5dd3258f3'
const task: ChildTask = {
  id: '1504027e-51a1-4203-ad5e-f990e7aedc22',
  tool_call_id: 'delegate',
  child_agent_id: 'bab30566-c732-4015-be55-f8bce0cb2697',
  role: 'researcher',
  run_id: 'b9203339-f811-41d2-a4eb-fa9d0a7a59fe',
  status: 'awaiting_approval',
  checkpointed: true,
  created_at: '2026-11-27T00:00:00Z',
  updated_at: '2026-11-27T00:00:01Z',
  result: null,
  instructions: [],
}

function ask(id = 'ask'): Interrupt {
  return {
    id,
    reason: 'moldy.ask_user',
    subagentRunId: childRun,
    metadata: {
      moldy: {
        type: 'ask_user',
        question: 'Which format?',
        options: ['HTML', 'PDF'],
      },
    },
  }
}

function approval(): Interrupt {
  return {
    id: 'approval',
    reason: 'moldy.tool_approval',
    subagentRunId: childRun,
    metadata: {
      moldy: {
        action_requests: [{ name: 'save_report', args: { file: 'report.txt' } }],
        review_configs: [{ action_name: 'save_report', allowed_decisions: ['approve', 'reject'] }],
      },
    },
  }
}

function auth(id: string, attempt: number): Interrupt {
  return {
    id,
    reason: 'moldy.auth_required',
    subagentRunId: childRun,
    metadata: {
      moldy: {
        type: 'auth_required',
        auth_request_id: 'same-auth-request',
        reason: 'auth_needed',
        attempt,
        items: [
          { resource_name: 'Private connector', fixable_by: 'owner', tool_names: ['read_report'] },
        ],
      },
    },
  }
}

function session(
  interrupts: Interrupt[],
  childOverrides: Record<string, unknown> = {},
): AgUiChatSessionState {
  const snapshot = parseWireChatSnapshot(
    {
      threadId: conversationId,
      protocolVersion: '1.0',
      projectionVersion: '1',
      interruptRunId: publicRun,
      physicalInterruptRunId: physicalRun,
      runId: publicRun,
      runStatus: 'interrupted',
      activeTipMessageId: 'assistant',
      messages: [
        {
          id: 'assistant',
          role: 'assistant',
          toolCalls: [
            {
              id: publicParentCall,
              type: 'function',
              function: { name: 'task', arguments: '{}' },
            },
          ],
        },
      ],
      messageLinks: [
        {
          messageId: 'assistant',
          uiMessageId: 'assistant',
          runId: publicRun,
          physicalRunId: physicalRun,
        },
      ],
      subagents: [
        {
          subagentRunId: childRun,
          runId: publicRun,
          physicalRunId: physicalRun,
          parentToolCallId: publicParentCall,
          parentMessageId: 'assistant',
          name: task.role,
          status: 'interrupted',
          childTaskId: task.id,
          interruptIds: interrupts.map((slot) => slot.id),
          ...childOverrides,
        },
      ],
      activities: [],
      interrupts,
    },
    conversationId,
  )
  const state = createAgUiChatSession({
    conversationId,
    readSnapshot: async () => snapshot,
    getServerMessages: () => [],
  }).getSnapshot()
  return {
    ...state,
    snapshot,
    subagents: snapshot.subagents,
    interrupts: snapshot.interrupts,
    messageLinks: snapshot.messageLinks,
    terminalSequence: 1,
    terminalRunId: publicRun,
    lastTerminalPhysicalRunId: physicalRun,
  }
}

const registerDecision = vi.fn().mockResolvedValue(undefined)
const onResumeDecisions = vi.fn()
const refetchDetail = vi.fn()
const refetchList = vi.fn()
const page = { enabled: true, allow_legacy_details: false, items: [task], next_offset: null }
const list = {
  enabled: true,
  isFetched: true,
  isPending: false,
  isError: false,
  error: null,
  hasNextPage: false,
  tasks: [task],
  refetch: refetchList,
}
const detail = { data: task, error: null, isError: false, isPending: false, refetch: refetchDetail }

function forms(
  state: AgUiChatSessionState,
  readSnapshot?: (signal?: AbortSignal) => Promise<WireChatSnapshot>,
) {
  return (
    <ChatWorkCommandConversationContext.Provider value={conversationId}>
      <HiTLContext.Provider value={{ registerDecision, onResumeDecisions }}>
        <AgUiChildPendingForms
          conversationId={conversationId}
          snapshot={state}
          readSnapshot={readSnapshot}
        />
      </HiTLContext.Provider>
    </ChatWorkCommandConversationContext.Provider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  wire.params.mockReturnValue({})
  wire.list.mockReturnValue(list)
  wire.detail.mockReturnValue(detail)
  refetchList.mockResolvedValue({ data: { pages: [page] }, isSuccess: true, error: null })
  refetchDetail.mockResolvedValue({ data: task, isSuccess: true, error: null })
})

describe('managed child interrupt forms', () => {
  it('renders a single question and registers the answer for the current server-bound child', async () => {
    const state = session([ask()])
    wire.interrupts.mockReturnValue(state.interrupts)
    render(forms(state))
    expect(screen.getAllByText('Which format?')).toHaveLength(1)
    fireEvent.click(screen.getByText('HTML'))
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }))
    await waitFor(() =>
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'respond', message: 'HTML' },
        'HTML',
        'ask',
      ),
    )
    expect(refetchDetail).toHaveBeenCalled()
    expect(onResumeDecisions).not.toHaveBeenCalled()
  })

  it('submits a tool approval and never targets the original turn by its physical run id', async () => {
    const state = session([approval()])
    wire.interrupts.mockReturnValue(state.interrupts)
    render(forms(state))
    fireEvent.click(screen.getByRole('button', { name: 'approve' }))
    await waitFor(() =>
      expect(registerDecision).toHaveBeenCalledWith(0, { type: 'approve' }, 'approved', 'approval'),
    )
    expect(registerDecision).toHaveBeenCalledTimes(1)
  })

  it('replaces an older auth question with the new id and sends the current attempt', async () => {
    const first = session([auth('old-id', 1)])
    wire.interrupts.mockReturnValue(first.interrupts)
    const view = render(forms(first))
    expect(screen.getByText('Private connector')).toBeVisible()
    const next = session([auth('new-id', 2)])
    wire.interrupts.mockReturnValue(next.interrupts)
    view.rerender(forms(next))
    fireEvent.click(screen.getByRole('button', { name: 'skip' }))
    await waitFor(() =>
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'respond', message: 'skip', attempt: 2 },
        'skip',
        'new-id',
      ),
    )
    expect(registerDecision).toHaveBeenCalledTimes(1)
  })

  it.each(['history', 'sdk'])(
    'hides an admitted child when the %s batch contains a read-only slot',
    (source) => {
      const readOnly: Interrupt = {
        id: 'restricted-slot',
        reason: 'moldy.input_required',
        metadata: { moldy: { actionable: false } },
      }
      const question = ask()
      const state = session(source === 'history' ? [question, readOnly] : [question], {
        interruptIds: [question.id],
      })
      wire.interrupts.mockReturnValue(source === 'sdk' ? [question, readOnly] : [question])
      render(forms(state))
      expect(screen.queryByText('Which format?')).toBeNull()
      expect(wire.detail).not.toHaveBeenCalled()
    },
  )

  it.each([
    { childTaskId: undefined },
    { runId: 'historical-run' },
    { physicalRunId: task.run_id },
    { parentMessageId: undefined },
    { interruptIds: ['another-slot'] },
    { subagentRunId: 'sibling-run' },
    { status: 'completed' },
  ])('suppresses the question for an unbound or mismatched child %j', (partial) => {
    const state = session([ask()], partial)
    wire.interrupts.mockReturnValue(state.interrupts)
    render(forms(state))
    expect(screen.queryByText('Which format?')).toBeNull()
    expect(registerDecision).not.toHaveBeenCalled()
  })

  it('removes all form data when a fresh list no longer authorizes the task', () => {
    const state = session([auth('slot', 1)])
    wire.interrupts.mockReturnValue(state.interrupts)
    const view = render(forms(state))
    expect(screen.getByText('Private connector')).toBeVisible()
    wire.list.mockReturnValue({ ...list, tasks: [] })
    view.rerender(forms(state))
    expect(screen.queryByText('Private connector')).toBeNull()
    expect(screen.queryByRole('button', { name: 'retry' })).toBeNull()
  })

  it('rejects the click if the exact child read loses access during submission', async () => {
    const state = session([approval()])
    wire.interrupts.mockReturnValue(state.interrupts)
    refetchDetail.mockResolvedValue({
      data: task,
      error: new Error('revoked'),
      isError: true,
      isSuccess: false,
    })
    render(forms(state))
    fireEvent.click(screen.getByRole('button', { name: 'approve' }))
    await waitFor(() => expect(refetchDetail).toHaveBeenCalled())
    expect(registerDecision).not.toHaveBeenCalled()
    expect(onResumeDecisions).not.toHaveBeenCalled()
  })

  it('drops a decision if the auth request rotates during the fresh permission read', async () => {
    const first = session([auth('old-id', 1)])
    wire.interrupts.mockReturnValue(first.interrupts)
    let finishRead: (() => void) | undefined
    refetchDetail.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishRead = () => resolve({ data: task, isSuccess: true, error: null })
        }),
    )
    const view = render(forms(first))
    fireEvent.click(screen.getByRole('button', { name: 'skip' }))
    await waitFor(() => expect(finishRead).toBeDefined())

    const current = session([auth('new-id', 2)])
    wire.interrupts.mockReturnValue(current.interrupts)
    view.rerender(forms(current))
    await act(async () => {
      finishRead?.()
    })
    expect(registerDecision).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'skip' }))
    await waitFor(() =>
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'respond', message: 'skip', attempt: 2 },
        'skip',
        'new-id',
      ),
    )
  })

  it('requires one owner per current slot and per durable task', () => {
    const state = session([ask()])
    const wireSnapshot = state.snapshot
    if (!wireSnapshot) throw new Error('Test requires durable snapshot')
    const child = wireSnapshot.subagents[0]
    wire.interrupts.mockReturnValue(state.interrupts)
    const other = { ...child, subagentRunId: 'unrelated-run', interruptIds: ['other-slot'] }
    const view = render(
      forms({
        ...state,
        snapshot: { ...wireSnapshot, subagents: [child, other] },
      }),
    )
    expect(screen.queryByText('Which format?')).toBeNull()
    view.rerender(
      forms({
        ...state,
        snapshot: {
          ...wireSnapshot,
          subagents: [
            child,
            {
              ...other,
              childTaskId: 'other-task',
              interruptIds: ['ask'],
            },
          ],
        },
      }),
    )
    expect(screen.queryByText('Which format?')).toBeNull()
  })

  it('accepts an off-page task only through its exact authorized detail', async () => {
    const state = session([approval()])
    wire.interrupts.mockReturnValue(state.interrupts)
    wire.list.mockReturnValue({ ...list, tasks: [], hasNextPage: true })
    refetchList.mockResolvedValue({
      data: { pages: [{ ...page, items: [], next_offset: 50 }] },
      isSuccess: true,
      error: null,
    })
    render(forms(state))
    expect(wire.detail).toHaveBeenCalledWith(conversationId, task.id)
    fireEvent.click(screen.getByRole('button', { name: 'approve' }))
    await waitFor(() => expect(registerDecision).toHaveBeenCalledTimes(1))
    expect(refetchDetail).toHaveBeenCalled()
  })

  it('rejects a decision when the refreshed list and exact detail disagree on the registered agent', async () => {
    const state = session([approval()])
    wire.interrupts.mockReturnValue(state.interrupts)
    refetchList.mockResolvedValue({
      data: {
        pages: [
          { ...page, items: [{ ...task, child_agent_id: '917d10f1-8d2e-4469-bbe7-7047c30701c3' }] },
        ],
      },
      error: null,
      isSuccess: true,
    })
    render(forms(state))
    fireEvent.click(screen.getByRole('button', { name: 'approve' }))
    await waitFor(() => expect(refetchList).toHaveBeenCalled())
    expect(registerDecision).not.toHaveBeenCalled()
  })

  it('suppresses every form immediately when the conversation read is denied', () => {
    const state = session([ask()])
    wire.interrupts.mockReturnValue(state.interrupts)
    render(forms({ ...state, readDenied: true }))
    expect(screen.queryByText('Which format?')).toBeNull()
    expect(wire.detail).not.toHaveBeenCalled()
  })

  it('removes the old form immediately when the SDK closes the interrupt', () => {
    const state = session([ask()])
    wire.interrupts.mockReturnValue(state.interrupts)
    const view = render(forms(state))
    expect(screen.getByText('Which format?')).toBeVisible()
    wire.interrupts.mockReturnValue([])
    view.rerender(forms(state))
    expect(screen.queryByText('Which format?')).toBeNull()
  })
})

function markLegacySlot(slot: Interrupt, fields: Record<string, unknown> = {}): Interrupt {
  const payload = slot.metadata?.moldy
  return {
    ...slot,
    metadata: {
      moldy: {
        ...(typeof payload === 'object' && payload !== null ? payload : {}),
        legacyInterruptAccess: true,
        ...fields,
      },
    },
  }
}

function legacySession(
  slots = [markLegacySlot(approval())],
  childOverrides: Record<string, unknown> = {},
): AgUiChatSessionState & { snapshot: WireChatSnapshot } {
  const state = session(slots, {
    childTaskId: undefined,
    legacyInterruptAccess: true,
    ...childOverrides,
  })
  if (!state.snapshot) throw new Error('Expected a saved child interrupt')
  return { ...state, snapshot: { ...state.snapshot, checkpointId: 'paused-checkpoint' } }
}

describe('legacy child interrupt access', () => {
  it('asks the server again before submitting the exact current child decision', async () => {
    const state = legacySession()
    const readSnapshot = vi.fn(async () => state.snapshot)
    wire.interrupts.mockReturnValue(state.interrupts)
    render(forms(state, readSnapshot))
    expect(readSnapshot).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'approve' }))
    await waitFor(() =>
      expect(registerDecision).toHaveBeenCalledWith(0, { type: 'approve' }, 'approved', 'approval'),
    )
    expect(readSnapshot).toHaveBeenCalledTimes(1)
    expect(wire.list).not.toHaveBeenCalled()
    expect(wire.detail).not.toHaveBeenCalled()
  })

  it.each([
    ['child marker absent', true, false],
    ['slot marker absent', false, true],
    ['both markers absent', false, false],
  ] as const)('withholds the form for %s', (_case, slotMarker, childMarker) => {
    const slot = slotMarker ? markLegacySlot(approval()) : approval()
    const state = legacySession([slot], { legacyInterruptAccess: childMarker || undefined })
    const readSnapshot = vi.fn(async () => state.snapshot)
    wire.interrupts.mockReturnValue(state.interrupts)
    render(forms(state, readSnapshot))
    expect(screen.queryByRole('button', { name: 'approve' })).toBeNull()
    expect(readSnapshot).not.toHaveBeenCalled()
    expect(wire.list).not.toHaveBeenCalled()
    expect(wire.detail).not.toHaveBeenCalled()
  })

  it('hides the entire batch when a sibling is marked read-only', () => {
    const sibling = markLegacySlot(
      { ...ask('sibling'), subagentRunId: 'other-child' },
      { actionable: false },
    )
    const state = legacySession([markLegacySlot(approval()), sibling], {
      interruptIds: ['approval'],
    })
    wire.interrupts.mockReturnValue(state.interrupts)
    render(
      forms(
        state,
        vi.fn(async () => state.snapshot),
      ),
    )
    expect(screen.queryByRole('button', { name: 'approve' })).toBeNull()
    expect(screen.queryByText('Which format?')).toBeNull()
    expect(wire.list).not.toHaveBeenCalled()
    expect(wire.detail).not.toHaveBeenCalled()
  })

  it('withholds sibling controls while a passive workflow slot has no user action', () => {
    const passive: Interrupt = {
      id: 'workflow-checkpoint',
      reason: 'moldy.workflow_wait',
      message: 'Waiting for a separate review.',
    }
    const state = legacySession([markLegacySlot(approval()), passive], {
      interruptIds: ['approval'],
    })
    wire.interrupts.mockReturnValue(state.interrupts)
    render(
      forms(
        state,
        vi.fn(async () => state.snapshot),
      ),
    )
    expect(screen.queryByRole('button', { name: 'approve' })).toBeNull()
    expect(wire.list).not.toHaveBeenCalled()
    expect(wire.detail).not.toHaveBeenCalled()
  })

  it.each(['checkpointId', 'activeTipMessageId'] as const)(
    'withholds the legacy form when the snapshot omits its %s',
    (key) => {
      const state = legacySession()
      wire.interrupts.mockReturnValue(state.interrupts)
      const incomplete = { ...state.snapshot, [key]: null }
      const readSnapshot = vi.fn(async () => incomplete)
      render(forms({ ...state, snapshot: incomplete }, readSnapshot))
      expect(screen.queryByRole('button', { name: 'approve' })).toBeNull()
      expect(readSnapshot).not.toHaveBeenCalled()
      expect(wire.list).not.toHaveBeenCalled()
      expect(wire.detail).not.toHaveBeenCalled()
    },
  )

  const changed = {
    'read permission revoked': () => Promise.reject(new Error('access revoked')),
    'child authorization revoked': () =>
      Promise.resolve(legacySession(undefined, { legacyInterruptAccess: undefined }).snapshot),
    'slot authorization revoked': () => Promise.resolve(legacySession([approval()]).snapshot),
    'checkpoint replaced': (snapshot: WireChatSnapshot) =>
      Promise.resolve({
        ...snapshot,
        checkpointId: 'sibling-checkpoint',
      }),
    'branch tip replaced': (snapshot: WireChatSnapshot) =>
      Promise.resolve({
        ...snapshot,
        activeTipMessageId: 'different-tip',
      }),
    'child run replaced': (snapshot: WireChatSnapshot) =>
      Promise.resolve({
        ...snapshot,
        physicalInterruptRunId: task.run_id,
      }),
    'another slot marked read-only': () =>
      Promise.resolve(
        legacySession(
          [
            markLegacySlot(approval()),
            markLegacySlot(
              { ...ask('other'), subagentRunId: 'other-child' },
              { actionable: false },
            ),
          ],
          { interruptIds: ['approval'] },
        ).snapshot,
      ),
  } satisfies Record<string, (snapshot: WireChatSnapshot) => Promise<WireChatSnapshot>>

  it.each(Object.entries(changed))(
    'discards a click when reauthorization reports %s',
    async (_reason, change) => {
      const state = legacySession()
      wire.interrupts.mockReturnValue(state.interrupts)
      const readSnapshot = vi.fn(() => change(state.snapshot))
      render(forms(state, readSnapshot))
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'approve' }))
      })
      expect(readSnapshot).toHaveBeenCalledTimes(1)
      expect(registerDecision).not.toHaveBeenCalled()
      expect(wire.list).not.toHaveBeenCalled()
      expect(wire.detail).not.toHaveBeenCalled()
    },
  )

  it('discards the approval if the active organization changes during the protected read', async () => {
    const state = legacySession()
    wire.interrupts.mockReturnValue(state.interrupts)
    const readSnapshot = vi.fn(async () => {
      beginOrgSwitch()
      finishOrgSwitch()
      return state.snapshot
    })
    render(forms(state, readSnapshot))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'approve' }))
    })
    expect(readSnapshot).toHaveBeenCalledTimes(1)
    expect(registerDecision).not.toHaveBeenCalled()
  })

  it.each(['retry', 'skip', 'cancel'])(
    'allows the auth response %s without exposing credential controls',
    async (response) => {
      wire.params.mockReturnValue({ agentId: 'visible-parent-agent' })
      const state = legacySession([
        markLegacySlot(auth('rotated-auth', 2), {
          items: [
            {
              resource_name: 'OAuth connection',
              tool_names: ['read_document'],
              fixable_by: 'me',
              credential_id: 'demo-oauth',
              connect: { kind: 'oauth' },
            },
            { resource_name: 'Agent binding', fixable_by: 'me', connect: { kind: 'binding' } },
            { resource_name: 'Owner connector', fixable_by: 'owner' },
          ],
        }),
      ])
      const readSnapshot = vi.fn(async () => state.snapshot)
      wire.interrupts.mockReturnValue(state.interrupts)
      render(forms(state, readSnapshot))
      expect(
        screen
          .getAllByRole('button')
          .map((button) => button.textContent)
          .sort(),
      ).toEqual(['cancel', 'retry', 'skip'])
      expect(screen.getByText('OAuth connection')).toBeVisible()
      expect(screen.getByText(/read_document/)).toBeVisible()
      expect(screen.getByText('Owner connector')).toBeVisible()
      expect(screen.getByText('ownerNotified')).toBeVisible()
      expect(screen.queryByText('demo-oauth')).toBeNull()
      expect(screen.queryByRole('link')).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: response }))
      await waitFor(() =>
        expect(registerDecision).toHaveBeenCalledWith(
          0,
          { type: 'respond', message: response, attempt: 2 },
          response,
          'rotated-auth',
        ),
      )
      expect(readSnapshot).toHaveBeenCalledTimes(1)
      expect(wire.list).not.toHaveBeenCalled()
      expect(wire.detail).not.toHaveBeenCalled()
    },
  )
})
