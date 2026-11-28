import { useSyncExternalStore } from 'react'
import { RunAgentInputSchema } from '@ag-ui/core/schemas'
import type { RunAgentInput } from '@ag-ui/core'
import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
import { QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { currentAgUiApprovalKey } from '@/lib/ag-ui/approval-admission'
import { useAgUiHitlActions } from '@/lib/ag-ui/chat-actions-interrupts'
import { useChatSession } from '@/lib/ag-ui/use-chat-session'
import { HiTLContext } from '@/lib/chat/hitl-context'
import { createTestQueryClient } from '../../../../tests/test-utils'
import { AgUiChildPendingForms } from '../ag-ui-child-pending-forms'

vi.mock('next/navigation', () => ({ useParams: () => ({}) }))
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))

const threadId = '3b7b8b91-171d-400b-a829-1b1880f11e6a'
const originalRun = '45e598ef-1367-43ed-83d2-35e4b2920ea3'
const physicalRun = '9508b3e2-9148-4b92-81f6-a395d28a84c5'
const childTask = {
  id: 'c40f23cb-ec6e-4f49-856e-0c3b07bc6bfa',
  child_agent_id: 'b23c140e-6a3b-4422-a4e2-e4b9f127585b',
  run_id: originalRun,
  tool_call_id: 'delegate',
  role: 'reviewer',
  status: 'awaiting_approval',
  checkpointed: true,
  created_at: '2026-11-27T00:00:00Z',
  updated_at: '2026-11-27T00:00:01Z',
  result: null,
  instructions: [],
}
const publicCallId = 'tool-63d99cc5-0fb4-41c6-a17a-602dfe0e1015'
const currentSlot = 'child-current-approval'

function pausedHistory() {
  return {
    threadId,
    protocolVersion: '1.0',
    projectionVersion: '1',
    checkpointId: 'paused-checkpoint',
    runId: 'paused-run',
    runStatus: 'interrupted',
    latestPhysicalRunId: physicalRun,
    interruptRunId: 'paused-run',
    physicalInterruptRunId: physicalRun,
    activeTipMessageId: 'parent',
    messages: [
      { id: 'question', role: 'user', content: 'Review the notes.' },
      {
        id: 'parent',
        role: 'assistant',
        content: 'Delegating review.',
        toolCalls: [
          {
            id: publicCallId,
            type: 'function',
            function: { name: 'task', arguments: '{}' },
          },
        ],
      },
    ],
    messageLinks: [
      { messageId: 'question', uiMessageId: 'question', parentMessageId: null },
      {
        messageId: 'parent',
        uiMessageId: 'parent',
        parentMessageId: 'question',
        runId: 'paused-run',
        physicalRunId: physicalRun,
      },
    ],
    subagents: [
      {
        subagentRunId: 'review-run',
        runId: 'paused-run',
        physicalRunId: physicalRun,
        parentToolCallId: publicCallId,
        parentMessageId: 'parent',
        name: 'reviewer',
        status: 'interrupted',
        childTaskId: childTask.id,
        interruptIds: [currentSlot],
      },
    ],
    activities: [],
    interrupts: [
      {
        id: currentSlot,
        subagentRunId: 'review-run',
        reason: 'moldy.tool_approval',
        metadata: {
          moldy: {
            action_requests: [{ name: 'save_notes', args: { file: 'notes.txt' } }],
            review_configs: [
              { action_name: 'save_notes', allowed_decisions: ['approve', 'reject'] },
            ],
          },
        },
      },
    ],
  }
}

function RestoredChildForm() {
  const { agent, session } = useChatSession({ conversationId: threadId, serverMessages: [] })
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot)
  const runtime = useAgUiRuntime({
    agent,
    adapters: { history: session.history },
    resumeTranscript: 'appended',
  })
  const actions = useAgUiHitlActions({
    runtime,
    conversationId: threadId,
    agentId: 'parent-agent',
    getReadyBatchKey: (pending) => currentAgUiApprovalKey(threadId, session.getSnapshot(), pending),
  })
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <HiTLContext.Provider value={actions}>
        <AgUiChildPendingForms
          conversationId={threadId}
          snapshot={state}
          readSnapshot={(signal) => agent.readSnapshot({ signal })}
        />
      </HiTLContext.Provider>
    </AssistantRuntimeProvider>
  )
}

function successfulRun(command: RunAgentInput, physical: string) {
  const frames = [
    { type: 'RUN_STARTED', runId: command.runId, threadId, protocolVersion: '1.0' },
    { type: 'RUN_FINISHED', runId: command.runId, threadId, outcome: { type: 'success' } },
  ]
  return new Response(frames.map((frame) => 'data: ' + JSON.stringify(frame) + '\n\n').join(''), {
    headers: {
      'content-type': 'text/event-stream',
      'X-Run-Id': command.runId,
      'X-Moldy-Run-Id': physical,
    },
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('restores a delegated approval through the official runtime and posts one native decision after a click', async () => {
  const reads: string[] = []
  const commands: RunAgentInput[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input, init = {}) => {
      const path = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
        .pathname
      if ((init.method ?? 'GET') === 'GET') {
        reads.push(path)
        if (path.endsWith('/snapshot')) return Response.json(pausedHistory())
        if (path.endsWith('/child-tasks'))
          return Response.json({
            items: [childTask],
            enabled: true,
            allow_legacy_details: false,
            next_offset: null,
          })
        if (path.endsWith('/child-tasks/' + childTask.id + '/wait')) return Response.json(childTask)
      }
      if (init.method === 'POST' && path.endsWith('/ag-ui/runs')) {
        const command = RunAgentInputSchema.parse(JSON.parse(String(init.body)))
        commands.push(command)
        return successfulRun(command, 'a0155e90-0761-42d6-8487-8a2554643614')
      }
      throw new Error('Unexpected request ' + init.method + ' ' + path)
    }),
  )
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <RestoredChildForm />
    </QueryClientProvider>,
  )
  const approve = await screen.findByRole('button', { name: 'approve' })
  expect(commands).toEqual([])
  expect(reads.some((path) => path.endsWith('/snapshot'))).toBe(true)
  expect(reads.some((path) => path.endsWith('/child-tasks/' + childTask.id + '/wait'))).toBe(true)
  fireEvent.click(approve)
  await waitFor(() => expect(commands).toHaveLength(1))
  expect(commands[0]).toMatchObject({
    threadId,
    messages: [],
    resume: [
      {
        interruptId: currentSlot,
        status: 'resolved',
        payload: { decisions: [{ type: 'approve' }] },
      },
    ],
  })
  expect(commands[0].runId).not.toBe('paused-run')
  await waitFor(() => expect(screen.queryByRole('button', { name: 'approve' })).toBeNull())
  expect(commands).toHaveLength(1)
})

it('reauthorizes a legacy child through one snapshot GET before submitting its native approval', async () => {
  const saved = pausedHistory()
  const legacy = {
    ...saved,
    checkpointId: 'current-checkpoint',
    subagents: saved.subagents.map((child) => ({
      ...child,
      childTaskId: undefined,
      legacyInterruptAccess: true,
    })),
    interrupts: saved.interrupts.map((slot) => ({
      ...slot,
      metadata: { moldy: { ...slot.metadata.moldy, legacyInterruptAccess: true } },
    })),
  }
  const requests: string[] = []
  const commands: RunAgentInput[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input, init = {}) => {
      const path = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
        .pathname
      const method = init.method ?? 'GET'
      requests.push(method + ' ' + path)
      if (method === 'GET' && path.endsWith('/ag-ui/snapshot')) return Response.json(legacy)
      if (method === 'POST' && path.endsWith('/ag-ui/runs')) {
        const body = RunAgentInputSchema.parse(JSON.parse(String(init.body)))
        commands.push(body)
        return successfulRun(body, 'e4d9a7b3-82d2-41ca-9a5a-87bcc5a06783')
      }
      throw new Error('Unexpected request ' + method + ' ' + path)
    }),
  )
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <RestoredChildForm />
    </QueryClientProvider>,
  )
  const base = '/api/conversations/' + threadId + '/ag-ui'
  const approve = await screen.findByRole('button', { name: 'approve' })
  expect(requests).toEqual(['GET ' + base + '/snapshot'])
  fireEvent.click(approve)
  await waitFor(() => expect(commands).toHaveLength(1))
  expect(commands[0]).toMatchObject({
    threadId,
    messages: [],
    resume: [
      {
        interruptId: currentSlot,
        status: 'resolved',
        payload: { decisions: [{ type: 'approve' }] },
      },
    ],
  })
  await waitFor(() => expect(screen.queryByRole('button', { name: 'approve' })).toBeNull())
  expect(requests).toEqual([
    'GET ' + base + '/snapshot',
    'GET ' + base + '/snapshot',
    'POST ' + base + '/runs',
  ])
})

it('reopens a credential checkpoint with an active binding and past OAuth popup using only reads', async () => {
  const oauthCredential = 'mock-oauth'
  const binding = { resource_name: 'Notes', fixable_by: 'me', tool_names: ['read_notes'] }
  const oauth = {
    resource_name: 'Mail',
    fixable_by: 'me',
    credential_id: oauthCredential,
    connect: { kind: 'oauth' },
  }
  const snapshot = {
    ...pausedHistory(),
    interrupts: [
      {
        id: currentSlot,
        subagentRunId: 'review-run',
        reason: 'moldy.auth_required',
        metadata: {
          moldy: {
            type: 'auth_required',
            auth_request_id: 'connect-mail',
            reason: 'expired',
            attempt: 2,
            items: [binding, oauth],
          },
        },
      },
    ],
  }
  const reads: string[] = []
  const writes: string[] = []
  const commands: RunAgentInput[] = []
  const oauthPath = '/api/oauth2-credential/auth/' + oauthCredential
  const snapshotPath = '/api/conversations/' + threadId + '/ag-ui/snapshot'
  const readinessPath = '/api/agents/' + childTask.child_agent_id + '/readiness'
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input, init = {}) => {
      const path = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
        .pathname
      const method = init.method ?? 'GET'
      if (method === 'GET') {
        reads.push(path)
        if (path === snapshotPath) return Response.json(snapshot)
        if (path.endsWith('/child-tasks'))
          return Response.json({
            items: [childTask],
            enabled: true,
            allow_legacy_details: false,
            next_offset: null,
          })
        if (path.endsWith('/child-tasks/' + childTask.id + '/wait')) return Response.json(childTask)
        if (path === readinessPath)
          return Response.json({
            overall: 'ready',
            items: [
              {
                kind: 'tool',
                id: 'notes',
                name: 'Notes',
                required: true,
                result: 'ready',
                credential: {
                  mode: 'per_user',
                  status: 'active',
                  credential_id: 'existing-binding',
                },
              },
            ],
          })
      } else {
        writes.push(method + ' ' + path)
        if (method === 'POST' && path === oauthPath)
          return Response.json({
            state: 'prior-session',
            authorization_url: 'https://oauth.example/authorize',
          })
        if (method === 'POST' && path.endsWith('/ag-ui/runs')) {
          const command = RunAgentInputSchema.parse(JSON.parse(String(init.body)))
          commands.push(command)
          return successfulRun(command, physicalRun)
        }
      }
      throw new Error('Unexpected request ' + method + ' ' + path)
    }),
  )
  const popup = { location: { href: 'about:blank' }, focus: vi.fn(), close: vi.fn() }
  const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)
  const restore = () =>
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <RestoredChildForm />
      </QueryClientProvider>,
    )
  try {
    const previous = restore()
    await screen.findByRole('link', { name: 'openCredentials' })
    expect(writes).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'connectOAuth' }))
    await screen.findByText('oauthWaiting')
    expect(writes).toEqual(['POST ' + oauthPath])
    previous.unmount()

    reads.length = 0
    writes.length = 0
    restore()
    await screen.findByRole('link', { name: 'openCredentials' })
    expect(screen.getByRole('button', { name: 'connectOAuth' })).toBeEnabled()
    expect(reads).toEqual(expect.arrayContaining([snapshotPath, readinessPath]))
    await act(async () => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { type: 'moldy.oauth.completed', credentialId: oauthCredential },
          origin: window.location.origin,
        }),
      )
    })
    expect(open).toHaveBeenCalledTimes(1)
    expect(writes).toEqual([])

    fireEvent.click(screen.getByRole('button', { name: 'retry' }))
    await waitFor(() => expect(commands).toHaveLength(1))
    expect(commands[0]?.resume).toMatchObject([
      {
        interruptId: currentSlot,
        payload: { decisions: [{ type: 'respond', message: 'retry', attempt: 2 }] },
      },
    ])
    expect(writes).toEqual(['POST /api/conversations/' + threadId + '/ag-ui/runs'])
  } finally {
    open.mockRestore()
  }
})
