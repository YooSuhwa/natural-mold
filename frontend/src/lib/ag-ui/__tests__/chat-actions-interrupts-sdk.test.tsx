import { HttpAgent } from '@ag-ui/client'
import type { RunAgentInput } from '@ag-ui/core'
import type { ThreadHistoryAdapter } from '@assistant-ui/react'
import { useAgUiRuntime, type AgUiInterrupt } from '@assistant-ui/react-ag-ui'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useAgUiHitlActions } from '../chat-actions-interrupts'

const threadId = '5963aae2-8aa7-47be-865e-95382bb2d596'
const prompt = 'Review the draft.'
const question = 'Approve the title and outline?'
const slot = {
  id: 'review-current',
  reason: 'moldy.tool_approval',
  message: question,
  metadata: {
    moldy: {
      action_requests: ['title', 'outline'].map((name) => ({ name, args: {} })),
      review_configs: ['title', 'outline'].map((name) => ({
        action_name: name,
        allowed_decisions: ['approve', 'reject'],
      })),
    },
  },
}

function historyFor(
  agent: HttpAgent,
  interrupts: HttpAgent['pendingInterrupts'],
): ThreadHistoryAdapter {
  return {
    async load() {
      agent.pendingInterrupts = interrupts
      return {
        headId: 'paused-assistant',
        messages: [
          {
            parentId: null,
            message: {
              id: 'first-user',
              role: 'user',
              content: [{ type: 'text', text: prompt }],
              createdAt: new Date(0),
              metadata: { custom: {} },
              attachments: [],
            },
          },
          {
            parentId: 'first-user',
            message: {
              id: 'paused-assistant',
              role: 'assistant',
              status: { type: 'requires-action', reason: 'interrupt' },
              content: [{ type: 'text', text: question }],
              createdAt: new Date(0),
              metadata: {
                unstable_state: null,
                unstable_annotations: [],
                unstable_data: [],
                steps: [],
                custom: { agui: { interrupts } },
              },
            },
          },
        ],
      }
    },
    append: vi.fn(),
  }
}

function interceptRuns() {
  const posts: RunAgentInput[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (url, init) => {
      const request = new Request(url, init)
      expect(request.method).toBe('POST')
      expect(new URL(request.url).pathname).toBe('/runs')
      const input: RunAgentInput = await request.json()
      posts.push(input)
      const resuming = Boolean(input.resume?.length)
      const messageId = resuming ? 'done-assistant' : 'paused-assistant'
      const events = [
        { type: 'RUN_STARTED', threadId, runId: input.runId, protocolVersion: '1.0' },
        { type: 'TEXT_MESSAGE_START', messageId, role: 'assistant' },
        { type: 'TEXT_MESSAGE_CONTENT', messageId, delta: resuming ? 'Review saved.' : question },
        { type: 'TEXT_MESSAGE_END', messageId },
        {
          type: 'RUN_FINISHED',
          threadId,
          runId: input.runId,
          outcome: resuming ? { type: 'success' } : { type: 'interrupt', interrupts: [slot] },
        },
      ]
      return new Response(
        events.map((event) => 'data: ' + JSON.stringify(event) + '\n\n').join(''),
        {
          headers: { 'content-type': 'text/event-stream' },
        },
      )
    }),
  )
  return posts
}

function openChat(
  interrupts?: HttpAgent['pendingInterrupts'],
  getReadyBatchKey: (native: readonly AgUiInterrupt[]) => string | null = (native) =>
    native.length ? 'confirmed-checkpoint' : null,
) {
  const posts = interceptRuns()
  const agent = new HttpAgent({ threadId, url: 'http://localhost/runs' })
  const history = interrupts ? historyFor(agent, interrupts) : undefined
  const onError = vi.fn()
  const { result, rerender } = renderHook(() => {
    const runtime = useAgUiRuntime({
      agent,
      adapters: { history },
      resumeTranscript: 'appended',
      onError,
    })
    const actions = useAgUiHitlActions({
      runtime,
      conversationId: threadId,
      agentId: 'agent-1',
      getReadyBatchKey,
    })
    return { runtime, actions }
  })
  return { result, rerender, posts, onError }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it.each(['restored', 'live'] as const)(
  'sends one standard resume when both actions of a %s approval are answered',
  async (source) => {
    const { result, posts, onError } = openChat(source === 'restored' ? [slot] : undefined)

    if (source === 'live') {
      await act(async () => {
        await result.current.runtime.thread.append({
          role: 'user',
          content: [{ type: 'text', text: prompt }],
        })
      })
    }
    await waitFor(() =>
      expect(result.current.runtime.unstable_getPendingInterrupts()).toEqual([slot]),
    )
    const initialCount = source === 'restored' ? 0 : 1
    expect(posts).toHaveLength(initialCount)
    const decide = result.current.actions.registerDecision
    const rejectedOutline = { type: 'reject' as const, message: 'Revise the outline.' }
    const first = decide(1, rejectedOutline, undefined, slot.id)
    expect(posts).toHaveLength(initialCount)
    await act(async () => {
      await Promise.all([first, decide(0, { type: 'approve' }, undefined, slot.id)])
    })

    expect(onError).not.toHaveBeenCalled()
    expect(posts).toHaveLength(initialCount + 1)
    expect(posts.at(-1)).toMatchObject({
      threadId,
      messages: [],
      resume: [
        {
          interruptId: slot.id,
          status: 'resolved',
          payload: { decisions: [{ type: 'approve' }, rejectedOutline] },
        },
      ],
    })
    expect(result.current.runtime.unstable_getPendingInterrupts()).toEqual([])
    expect(result.current.runtime.thread.getState().messages.at(-1)?.content).toContainEqual({
      type: 'text',
      text: 'Review saved.',
    })
  },
)

it('keeps a restored mixed approval batch pending when one native interrupt is read-only', async () => {
  const current = {
    id: 'current-question',
    reason: 'moldy.ask_user',
    responseSchema: { type: 'object' },
    metadata: { moldy: { type: 'ask_user', question: 'Keep the title?' } },
  }
  const readOnly = {
    id: 'previous-question',
    reason: 'moldy.ask_user',
    metadata: { moldy: { type: 'ask_user', question: 'Keep the outline?', actionable: false } },
  }
  const { result, posts, onError } = openChat([current, readOnly])
  const pending = () => result.current.runtime.unstable_getPendingInterrupts()
  await waitFor(() => expect(pending()).toEqual([current, readOnly]))
  expect(posts).toHaveLength(0)
  // Both cards look like known ask_user questions. The backend marked only
  // the historical slot read-only; neither card can commit the mixed batch.
  const answer = { type: 'respond' as const, message: 'Yes.' }
  await act(async () => {
    const votes = await Promise.allSettled(
      [current, readOnly].map(({ id }) =>
        result.current.actions.registerDecision(0, answer, undefined, id),
      ),
    )
    for (const vote of votes) {
      expect(vote).toMatchObject({ status: 'rejected', reason: { name: 'InvalidStateError' } })
    }
  })
  expect(onError).not.toHaveBeenCalled()
  expect(pending()).toEqual([current, readOnly])
  expect(posts).toHaveLength(0)
})

it('waits for a rendered durable checkpoint before accepting a live SDK interrupt', async () => {
  let checkpoint: string | null = null
  const { result, rerender, posts, onError } = openChat(undefined, (native) =>
    native.length === 1 && native[0].id === slot.id ? checkpoint : null,
  )
  const decisions = [{ type: 'approve' }, { type: 'approve' }] as const
  await act(async () => {
    await result.current.runtime.thread.append({
      role: 'user',
      content: [{ type: 'text', text: prompt }],
    })
  })
  await waitFor(() =>
    expect(result.current.runtime.unstable_getPendingInterrupts()).toEqual([slot]),
  )
  expect(result.current.actions.canRespond).toBe(false)
  const earlyVote = result.current.actions.onResumeDecisions
  await act(async () => {
    await expect(earlyVote([...decisions])).rejects.toMatchObject({ name: 'InvalidStateError' })
  })
  expect(posts).toHaveLength(1)
  expect(posts[0].resume).toBeUndefined()

  checkpoint = 'checkpoint-after-stream'
  rerender()
  expect(result.current.actions.canRespond).toBe(true)
  await expect(earlyVote([...decisions])).rejects.toMatchObject({ name: 'InvalidStateError' })
  await act(async () => {
    await result.current.actions.onResumeDecisions([...decisions])
  })
  expect(onError).not.toHaveBeenCalled()
  expect(posts).toHaveLength(2)
  expect(posts[1]).toMatchObject({
    threadId,
    messages: [],
    resume: [{ interruptId: slot.id, status: 'resolved', payload: { decisions } }],
  })
  expect(result.current.runtime.unstable_getPendingInterrupts()).toEqual([])
})
