import { useSyncExternalStore } from 'react'
import { RunAgentInputSchema } from '@ag-ui/core/schemas'
import type { RunAgentInput } from '@ag-ui/core'
import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
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
const currentSlot = 'child-current-approval'

function pausedHistory() {
  return {
    threadId,
    protocolVersion: '1.0',
    projectionVersion: '1',
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
            id: childTask.tool_call_id,
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
        parentToolCallId: childTask.tool_call_id,
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
  const actions = useAgUiHitlActions({ runtime, conversationId: threadId, agentId: 'parent-agent' })
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <HiTLContext.Provider value={actions}>
        <AgUiChildPendingForms conversationId={threadId} snapshot={state} />
      </HiTLContext.Provider>
    </AssistantRuntimeProvider>
  )
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
        const frames = [
          { type: 'RUN_STARTED', runId: command.runId, threadId, protocolVersion: '1.0' },
          { type: 'RUN_FINISHED', runId: command.runId, threadId, outcome: { type: 'success' } },
        ]
        return new Response(
          frames.map((frame) => 'data: ' + JSON.stringify(frame) + '\n\n').join(''),
          {
            headers: {
              'content-type': 'text/event-stream',
              'X-Run-Id': command.runId,
              'X-Moldy-Run-Id': 'a0155e90-0761-42d6-8487-8a2554643614',
            },
          },
        )
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
