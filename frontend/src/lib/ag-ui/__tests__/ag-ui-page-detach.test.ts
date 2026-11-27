import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MoldyAgUiAgent } from '../moldy-agent'
import { useChatSession } from '../use-chat-session'
import { useDurableAgUiHistory } from '../use-durable-ag-ui-history'

const conversationId = '477242af-e949-4c1a-8718-420f2a0fec62'
const physicalRunId = '8ea7f98f-01d8-4d8c-ab65-84f85ade1aea'
const publicRunId = 'published-run'
const base = `/api/conversations/${conversationId}/ag-ui`

function snapshot(activeRunId: string | null) {
  return Response.json({
    threadId: conversationId,
    protocolVersion: '1.0',
    projectionVersion: '1',
    checkpointId: null,
    activeTipMessageId: null,
    activeRunId,
    physicalRunId: activeRunId ? physicalRunId : null,
    cursor: null,
    messages: [],
    messageLinks: [],
    subagents: [],
    activities: [],
    interrupts: [],
  })
}

function streamingResponse(runId: string, signal: AbortSignal) {
  const failure = new TypeError('network error')
  let output!: ReadableStreamDefaultController<Uint8Array>
  const response = new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        output = controller
        const event = {
          type: 'RUN_STARTED',
          threadId: conversationId,
          runId,
          protocolVersion: '1.0',
        }
        controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
        // Chromium can report a body failure after a successful SSE command or replay.
        signal.addEventListener('abort', () => controller.error(failure), { once: true })
      },
    }),
    {
      headers: {
        'Content-Type': 'text/event-stream',
        'X-Run-Id': runId,
        'X-Moldy-Run-Id': physicalRunId,
      },
    },
  )
  return { response, signal, fail: () => output.error(failure), failure }
}

function intercept(startActive: boolean) {
  const calls: { method: string; url: URL }[] = []
  const readers: ReturnType<typeof streamingResponse>[] = []
  let activeRunId: string | null = startActive ? publicRunId : null
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input, init = {}) => {
      const url = new URL(input instanceof Request ? input.url : input.toString())
      const method = init.method ?? 'GET'
      calls.push({ method, url })
      if (method === 'GET' && url.pathname === `${base}/snapshot`) return snapshot(activeRunId)
      if (method === 'POST' && url.pathname === `${base}/runs` && typeof init.body === 'string') {
        const command: unknown = JSON.parse(init.body)
        if (
          !command ||
          typeof command !== 'object' ||
          !('runId' in command) ||
          typeof command.runId !== 'string'
        )
          throw new Error('Missing run ID on an ordinary chat command')
        activeRunId = command.runId
      } else if (
        method !== 'GET' ||
        url.pathname !== `${base}/stream` ||
        url.searchParams.get('runId') !== activeRunId
      ) {
        throw new Error('Unexpected chat command or replay')
      }
      if (!activeRunId || !init.signal) throw new Error('Expected a bound streaming request')
      const reader = streamingResponse(activeRunId, init.signal)
      readers.push(reader)
      return reader.response
    }),
  )
  return { calls, readers }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('AG-UI document detachment', () => {
  it.each([
    { source: 'command', cached: false },
    { source: 'command', cached: true },
    { source: 'replay', cached: true },
  ] as const)(
    'detaches an accepted $source stream on pagehide (persisted=$cached)',
    async ({ source, cached }) => {
      const { calls, readers } = intercept(source === 'replay')
      const error = vi.spyOn(console, 'error')
      const view = renderHook(() => {
        const { agent, session } = useChatSession({ conversationId, serverMessages: [] })
        const runtime = useAgUiRuntime({ agent, adapters: { history: session.history } })
        useDurableAgUiHistory({ runtime, session, agent, conversationId })
        return { runtime, session }
      })
      try {
        await waitFor(() => expect(view.result.current.session.getSnapshot().snapshot).toBeTruthy())
        if (source === 'command') {
          act(() => {
            void view.result.current.runtime.thread.append({
              role: 'user',
              content: [{ type: 'text', text: 'hello' }],
            })
          })
        }
        await waitFor(() =>
          expect(view.result.current.session.getSnapshot().activeRunId).toBeTruthy(),
        )
        await waitFor(() => expect(readers).toHaveLength(1))
        const beforeHide = calls.length
        await act(async () => {
          window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: cached }))
        })
        expect(readers[0].signal.aborted).toBe(true)
        await waitFor(() =>
          expect(view.result.current.runtime.thread.getState().isRunning).toBe(false),
        )
        expect(calls).toHaveLength(beforeHide)
        expect(error).not.toHaveBeenCalledWith('Agent execution failed:', expect.anything())
        if (!cached) return

        await act(async () => {
          window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))
        })
        await waitFor(() => expect(readers).toHaveLength(2))
        expect(readers[1].signal.aborted).toBe(false)
        expect(calls.slice(beforeHide).map(({ method, url }) => [method, url.pathname])).toEqual([
          ['GET', `${base}/snapshot`],
          ['GET', `${base}/stream`],
        ])
        expect(calls.filter(({ method }) => method === 'POST')).toHaveLength(
          source === 'command' ? 1 : 0,
        )
      } finally {
        view.unmount()
      }
    },
  )

  it.each(['command', 'replay'] as const)(
    'retains a genuine network failure on a live %s body',
    async (source) => {
      const { calls, readers } = intercept(source === 'replay')
      const error = vi.spyOn(console, 'error')
      const agent = new MoldyAgUiAgent({ conversationId })
      agent.messages = [{ id: 'question', role: 'user', content: 'hello' }]
      const started = vi.fn()
      const result =
        source === 'replay'
          ? agent.connectAgent({ runId: publicRunId }, { onRunStartedEvent: started })
          : agent.runAgent({ runId: publicRunId }, { onRunStartedEvent: started })
      try {
        await waitFor(() => expect(started).toHaveBeenCalledOnce())
        const reader = readers[0]
        expect(reader.signal.aborted).toBe(false)
        const rejected = expect(result).rejects.toThrow('network error')
        reader.fail()
        await rejected
        expect(error).toHaveBeenCalledWith('Agent execution failed:', reader.failure)
        expect(calls.map(({ method }) => method)).toEqual(
          source === 'replay' ? ['GET', 'GET'] : ['POST'],
        )
      } finally {
        agent.dispose()
      }
    },
  )

  it('reports a rejected HTTP command to the SDK before opening a stream', async () => {
    const fetchCommand = vi.fn<typeof fetch>(async () =>
      Response.json(
        { error: { code: 'WORKER_UNAVAILABLE', message: 'unavailable' } },
        { status: 503 },
      ),
    )
    vi.stubGlobal('fetch', fetchCommand)
    const agent = new MoldyAgUiAgent({ conversationId })
    agent.messages = [{ id: 'question', role: 'user', content: 'hello' }]
    try {
      await expect(agent.runAgent({ runId: publicRunId })).rejects.toMatchObject({
        name: 'ApiError',
        code: 'WORKER_UNAVAILABLE',
        status: 503,
      })
      expect(fetchCommand).toHaveBeenCalledOnce()
    } finally {
      agent.dispose()
    }
  })
})
