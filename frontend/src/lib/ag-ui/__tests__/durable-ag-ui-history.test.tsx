import { StrictMode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dispatchMoldyBranchSwitched } from '@/lib/chat/langgraph-runtime/branch-switch-events'
import { createAgUiChatSession } from '../chat-history'
import { MoldyAgUiAgent } from '../moldy-agent'
import { useDurableAgUiHistory } from '../use-durable-ag-ui-history'

const conversationId = '08a60eaf-a191-43a4-b2be-f13c370d7abf'
const publicRunId = 'public-active'
const physicalRunId = '6278c049-12aa-4a4b-be19-704728be80fd'

function savedChat(tip: string | null, fields: Record<string, unknown> = {}) {
  const messages = [{ id: 'question', role: 'user', content: '질문' }]
  if (tip !== null) messages.push({ id: tip, role: 'assistant', content: tip })
  return {
    threadId: conversationId,
    protocolVersion: '1.0',
    projectionVersion: '1',
    checkpointId: tip,
    activeTipMessageId: tip ?? 'question',
    activeRunId: null,
    physicalRunId: null,
    interruptRunId: null,
    physicalInterruptRunId: null,
    cursor: null,
    messages,
    messageLinks: messages.map(({ id, role }) => ({
      messageId: id,
      uiMessageId: id,
      parentMessageId: role === 'user' ? null : 'question',
      runId: publicRunId,
      physicalRunId,
      checkpointId: tip,
      parentCheckpointId: null,
    })),
    activities: [],
    subagents: [],
    interrupts: [],
    ...fields,
  }
}

interface RequestLog {
  method: string
  url: URL
}

function intercept(reply: (call: RequestLog) => Response | Promise<Response>) {
  const requests: RequestLog[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input, init = {}) => {
      const call = {
        method: init.method ?? 'GET',
        url: new URL(input instanceof Request ? input.url : input.toString(), 'http://localhost'),
      }
      requests.push(call)
      return reply(call)
    }),
  )
  return requests
}

function mountChat() {
  const agent: MoldyAgUiAgent = new MoldyAgUiAgent({
    conversationId,
    onPublicEvent: (event, context) => session.onPublicEvent(event, context),
  })
  const session = createAgUiChatSession({
    conversationId,
    readSnapshot: (signal?: AbortSignal) => agent.readSnapshot({ signal }),
    getServerMessages: () => [],
  })
  const onSettled = vi.fn()
  const view = renderHook(
    () => {
      const runtime = useAgUiRuntime({ agent, adapters: { history: session.history } })
      const busy = useDurableAgUiHistory({ runtime, session, agent, conversationId, onSettled })
      return { runtime, busy }
    },
    { wrapper: StrictMode },
  )
  return { ...view, onSettled }
}

function textFrame(frame: object) {
  return new TextEncoder().encode('data: ' + JSON.stringify(frame) + '\n\n')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('durable official chat history', () => {
  it('opens a completed conversation without starting or subscribing to a run', async () => {
    const calls = intercept(() => Response.json(savedChat('saved-answer')))
    const view = mountChat()
    await waitFor(() =>
      expect(view.result.current.runtime.thread.getState().messages.at(-1)?.id).toBe(
        'saved-answer',
      ),
    )
    expect(view.result.current.busy).toBe(false)
    expect(calls.length).toBeGreaterThan(0)
    expect(
      calls.every(({ method, url }) => method === 'GET' && url.pathname.endsWith('/snapshot')),
    ).toBe(true)
    expect(view.onSettled).not.toHaveBeenCalled()
  })

  it('restores the current approval without opening a stream or sending a resume command', async () => {
    const current = { id: 'rotated-approval', reason: 'input_required', message: '확인해 주세요.' }
    const calls = intercept(() =>
      Response.json(
        savedChat('paused-answer', {
          interruptRunId: publicRunId,
          physicalInterruptRunId: physicalRunId,
          interrupts: [current],
        }),
      ),
    )
    const view = mountChat()
    await waitFor(() =>
      expect(
        view.result.current.runtime.unstable_getPendingInterrupts().map(({ id }) => id),
      ).toEqual(['rotated-approval']),
    )
    expect(
      calls.every(({ url, method }) => method === 'GET' && url.pathname.endsWith('/snapshot')),
    ).toBe(true)
    expect(view.onSettled).not.toHaveBeenCalled()
  })

  it('reattaches once through GET and replaces the transient answer with the committed message', async () => {
    let committed = false
    let finish!: () => void
    const calls = intercept(({ url }) => {
      if (url.pathname.endsWith('/snapshot')) {
        return Response.json(
          committed
            ? savedChat('database-answer')
            : savedChat(null, { activeRunId: publicRunId, physicalRunId }),
        )
      }
      if (!url.pathname.endsWith('/stream')) throw new Error('Unexpected request: ' + url.pathname)
      const frames = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            textFrame({
              type: 'RUN_STARTED',
              threadId: conversationId,
              runId: publicRunId,
              protocolVersion: '1.0',
            }),
          )
          controller.enqueue(
            textFrame({
              type: 'TEXT_MESSAGE_START',
              messageId: 'transient-answer',
              role: 'assistant',
            }),
          )
          controller.enqueue(
            textFrame({
              type: 'TEXT_MESSAGE_CONTENT',
              messageId: 'transient-answer',
              delta: '진행 중',
            }),
          )
          finish = () => {
            committed = true
            controller.enqueue(
              textFrame({ type: 'TEXT_MESSAGE_END', messageId: 'transient-answer' }),
            )
            controller.enqueue(
              textFrame({
                type: 'RUN_FINISHED',
                threadId: conversationId,
                runId: publicRunId,
                outcome: { type: 'success' },
              }),
            )
            controller.close()
          }
        },
      })
      return new Response(frames, { headers: { 'Content-Type': 'text/event-stream' } })
    })
    const view = mountChat()
    await waitFor(() => expect(view.result.current.busy).toBe(true))
    await waitFor(() =>
      expect(view.result.current.runtime.thread.getState().messages.at(-1)?.content).toEqual([
        expect.objectContaining({ type: 'text', text: '진행 중' }),
      ]),
    )
    await act(async () => {
      finish()
    })
    await waitFor(() =>
      expect(view.result.current.runtime.thread.getState().messages.map(({ id }) => id)).toEqual([
        'question',
        'database-answer',
      ]),
    )
    expect(view.result.current.busy).toBe(false)
    expect(view.onSettled).toHaveBeenCalledTimes(1)
    const streams = calls.filter(({ url }) => url.pathname.endsWith('/stream'))
    expect(streams).toHaveLength(1)
    expect(streams[0].url.searchParams.get('runId')).toBe(publicRunId)
    expect(calls.every(({ method }) => method === 'GET')).toBe(true)
  })

  it('replays the same public run after a truncated stream and keeps a single durable answer', async () => {
    let streamCount = 0
    const calls = intercept(({ url }) => {
      if (url.pathname.endsWith('/snapshot')) {
        return Response.json(
          streamCount < 2
            ? savedChat(null, { activeRunId: publicRunId, physicalRunId })
            : savedChat('restored-once'),
        )
      }
      if (!url.pathname.endsWith('/stream')) throw new Error('Unexpected request: ' + url.pathname)
      streamCount++
      const frameOwner = { threadId: conversationId, runId: publicRunId, protocolVersion: '1.0' }
      const frames = [
        { type: 'RUN_STARTED', ...frameOwner },
        { type: 'TEXT_MESSAGE_START', messageId: 'wire-answer', role: 'assistant' },
        { type: 'TEXT_MESSAGE_CONTENT', messageId: 'wire-answer', delta: '응답' },
        ...(streamCount === 1
          ? []
          : [
              { type: 'TEXT_MESSAGE_END', messageId: 'wire-answer' },
              { type: 'RUN_FINISHED', ...frameOwner, outcome: { type: 'success' } },
            ]),
      ]
      return new Response(
        frames.map((frame) => 'data: ' + JSON.stringify(frame) + '\n\n').join(''),
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    })
    const view = mountChat()
    await waitFor(
      () =>
        expect(view.result.current.runtime.thread.getState().messages.map(({ id }) => id)).toEqual([
          'question',
          'restored-once',
        ]),
      { timeout: 5000 },
    )
    expect(view.onSettled).toHaveBeenCalledTimes(1)
    expect(
      calls
        .filter(({ url }) => url.pathname.endsWith('/stream'))
        .map(({ url }) => url.searchParams.get('runId')),
    ).toEqual([publicRunId, publicRunId])
    expect(calls.every(({ method }) => method === 'GET')).toBe(true)
  })

  it('ignores other conversations and prevents an older branch read from replacing a later selection', async () => {
    let respond: () => Response | Promise<Response> = () => Response.json(savedChat('original'))
    const calls = intercept(() => respond())
    const view = mountChat()
    const currentHead = () => view.result.current.runtime.thread.getState().messages.at(-1)?.id
    await waitFor(() => expect(currentHead()).toBe('original'))
    const initialRequests = calls.length
    await act(async () =>
      dispatchMoldyBranchSwitched({ conversationId: 'other', checkpointId: 'unrelated' }),
    )
    expect(calls).toHaveLength(initialRequests)

    let resolveEarlier!: (response: Response) => void
    respond = () =>
      new Promise<Response>((resolve) => {
        resolveEarlier = resolve
      })
    await act(async () => dispatchMoldyBranchSwitched({ conversationId, checkpointId: 'earlier' }))
    await waitFor(() => expect(resolveEarlier).toBeDefined())
    respond = () => Response.json(savedChat('later'))
    await act(async () => dispatchMoldyBranchSwitched({ conversationId, checkpointId: 'later' }))
    await waitFor(() => expect(currentHead()).toBe('later'))
    await act(async () => resolveEarlier(Response.json(savedChat('earlier'))))
    expect(currentHead()).toBe('later')
    expect(
      calls.every(({ method, url }) => method === 'GET' && url.pathname.endsWith('/snapshot')),
    ).toBe(true)
  })
})
