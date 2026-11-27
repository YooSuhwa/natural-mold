import { HttpAgent } from '@ag-ui/client'
import type { RunAgentInput } from '@ag-ui/core'
import type { ThreadHistoryAdapter } from '@assistant-ui/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
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

function historyFor(agent: HttpAgent): ThreadHistoryAdapter {
  return {
    async load() {
      agent.pendingInterrupts = [slot]
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
                custom: { agui: { interrupts: [slot] } },
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

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it.each(['restored', 'live'] as const)(
  'sends one standard resume when both actions of a %s approval are answered',
  async (source) => {
    const posts = interceptRuns()
    const agent = new HttpAgent({ threadId, url: 'http://localhost/runs' })
    const history = source === 'restored' ? historyFor(agent) : undefined
    const onError = vi.fn()
    const { result } = renderHook(() => {
      const runtime = useAgUiRuntime({
        agent,
        adapters: { history },
        resumeTranscript: 'appended',
        onError,
      })
      const actions = useAgUiHitlActions({ runtime, conversationId: threadId, agentId: 'agent-1' })
      return { runtime, actions }
    })

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
    if (!decide) throw new Error('Missing card action handler')
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
