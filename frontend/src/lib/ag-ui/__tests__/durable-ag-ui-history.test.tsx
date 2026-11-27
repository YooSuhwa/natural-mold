import { StrictMode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dispatchMoldyBranchSwitched } from '@/lib/chat/langgraph-runtime/branch-switch-events'
import { useChatSession } from '../use-chat-session'
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
  body: RequestInit['body']
  signal: RequestInit['signal']
}

function intercept(reply: (call: RequestLog) => Response | Promise<Response>) {
  const requests: RequestLog[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input, init = {}) => {
      const call = {
        method: init.method ?? 'GET',
        body: init.body,
        signal: init.signal ?? (input instanceof Request ? input.signal : undefined),
        url: new URL(input instanceof Request ? input.url : input.toString(), 'http://localhost'),
      }
      requests.push(call)
      return reply(call)
    }),
  )
  return requests
}

function mountChat(readOnly = false) {
  const onSettled = vi.fn()
  const view = renderHook(
    ({ serverActiveRunId }: { serverActiveRunId: string | null }) => {
      const { agent, session } = useChatSession({ conversationId, serverMessages: [] })
      const runtime = useAgUiRuntime({ agent, adapters: { history: session.history } })
      const busy = useDurableAgUiHistory({
        runtime,
        session,
        agent,
        conversationId,
        readOnly,
        serverActiveRunId,
        onSettled,
      })
      return { runtime, busy, session }
    },
    { wrapper: StrictMode, initialProps: { serverActiveRunId: null } },
  )
  return { ...view, onSettled, session: view.result.current.session }
}

function textFrame(frame: object) {
  return new TextEncoder().encode('data: ' + JSON.stringify(frame) + '\n\n')
}

function openSubmission(call: RequestLog) {
  if (typeof call.body !== 'string') throw new Error('Expected a JSON chat command')
  const { runId, resume }: { runId: string; resume?: unknown[] } = JSON.parse(call.body)
  let output!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      output = controller
      controller.enqueue(
        textFrame({ type: 'RUN_STARTED', threadId: conversationId, runId, protocolVersion: '1.0' }),
      )
      controller.enqueue(
        textFrame({ type: 'TEXT_MESSAGE_START', messageId: 'new-stream', role: 'assistant' }),
      )
      controller.enqueue(
        textFrame({
          type: 'TEXT_MESSAGE_CONTENT',
          messageId: 'new-stream',
          delta: '새 응답 작성 중',
        }),
      )
    },
  })
  return {
    response: new Response(body, {
      headers: {
        'Content-Type': 'text/event-stream',
        'X-Run-Id': runId,
        'X-Moldy-Run-Id': physicalRunId,
      },
    }),
    close(terminal: boolean) {
      if (terminal) {
        output.enqueue(textFrame({ type: 'TEXT_MESSAGE_END', messageId: 'new-stream' }))
        output.enqueue(
          textFrame({
            type: 'RUN_FINISHED',
            runId,
            threadId: conversationId,
            outcome: { type: 'success' },
          }),
        )
      }
      output.close()
    },
    snapshot: () => {
      const previous = savedChat('initial')
      const newMessages = [{ id: 'saved-turn', role: 'assistant', content: '저장된 새 응답' }]
      if (!resume?.length)
        newMessages.unshift({ id: 'saved-question', role: 'user', content: '새 질문' })
      return {
        ...previous,
        checkpointId: 'saved-turn',
        activeTipMessageId: 'saved-turn',
        messages: [...previous.messages, ...newMessages],
        messageLinks: [
          ...previous.messageLinks,
          ...newMessages.map(({ id }, i) => ({
            messageId: id,
            uiMessageId: id,
            parentMessageId: i === 0 ? 'initial' : 'saved-question',
            runId,
            physicalRunId,
            checkpointId: 'saved-turn',
            parentCheckpointId: 'initial',
          })),
        ],
      }
    },
  }
}

function childApprovalSnapshot(eligible: boolean) {
  const head = 'saved-parent-answer'
  const child = {
    name: 'Worker',
    subagentRunId: 'legacy-child',
    parentToolCallId: 'child-task-call',
    parentMessageId: head,
    runId: publicRunId,
    physicalRunId,
    status: 'interrupted',
    interruptIds: ['child-slot'],
    legacyInterruptAccess: true,
  }
  const slot = {
    id: 'child-slot',
    reason: 'moldy.ask_user',
    subagentRunId: eligible ? child.subagentRunId : 'different-child',
    message: '승인 질문',
    metadata: { moldy: { type: 'ask_user', question: '승인 질문', legacyInterruptAccess: true } },
  }
  return savedChat(head, {
    interruptRunId: publicRunId,
    physicalInterruptRunId: physicalRunId,
    subagents: [child],
    interrupts: [slot],
    messages: [
      { id: 'question', role: 'user', content: '질문' },
      {
        id: head,
        role: 'assistant',
        content: '저장된 부모 답변',
        toolCalls: [
          {
            id: child.parentToolCallId,
            type: 'function',
            function: { name: 'task', arguments: '{}' },
          },
        ],
      },
    ],
  })
}

const advance = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
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

  it.each([
    { delayedRead: false, resume: false, terminal: false, name: 'a lost terminal frame' },
    {
      delayedRead: true,
      resume: false,
      terminal: true,
      name: 'a branch read racing a new message',
    },
    {
      delayedRead: true,
      resume: true,
      terminal: true,
      name: 'a focus read racing an approval response',
    },
  ])('imports the accepted POST exactly once after $name', async (scenario) => {
    const { delayedRead, resume, terminal } = scenario
    let submitted: ReturnType<typeof openSubmission> | undefined
    let accept!: () => void
    let releaseRead: ((response: Response) => void) | undefined
    let pendingRequest: RequestLog | undefined
    let refreshing = false
    let stored = false
    const interrupt = { id: 'current-approval', reason: 'input_required', message: 'Continue?' }
    const decision = {
      interruptId: interrupt.id,
      status: 'resolved' as const,
      payload: { decisions: [{ type: 'approve' }] },
    }
    const initial = savedChat(
      'initial',
      resume
        ? {
            interruptRunId: publicRunId,
            physicalInterruptRunId: physicalRunId,
            interrupts: [interrupt],
          }
        : {},
    )
    const calls = intercept((call) => {
      if (call.method === 'POST' && call.url.pathname.endsWith('/runs')) {
        submitted = openSubmission(call)
        if (resume) {
          const { response } = submitted
          return new Promise<Response>((resolve) => {
            accept = () => resolve(response)
          })
        }
        return submitted.response
      }
      if (!call.url.pathname.endsWith('/snapshot'))
        throw new Error('Unexpected connection: ' + call.url.pathname)
      if (submitted && stored) return Response.json(submitted.snapshot())
      if (refreshing && !releaseRead) {
        pendingRequest = call
        return new Promise<Response>((resolve) => {
          releaseRead = resolve
        })
      }
      return Response.json(initial)
    })
    const view = mountChat()
    const thread = view.result.current.runtime.thread
    await waitFor(() => expect(thread.getState().messages.at(-1)?.id).toBe('initial'))
    if (delayedRead) {
      refreshing = true
      if (resume) {
        vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
        await act(async () => {
          window.dispatchEvent(new Event('focus'))
        })
      } else {
        await act(async () =>
          dispatchMoldyBranchSwitched({ conversationId, checkpointId: 'stale' }),
        )
      }
      await waitFor(() => expect(pendingRequest?.signal?.aborted).toBe(false))
      expect(pendingRequest?.url.search).toBe('')
    }
    act(() => {
      if (resume) void view.result.current.runtime.unstable_submitInterruptResponses([decision])
      else void thread.append({ role: 'user', content: [{ type: 'text', text: '새 질문' }] })
    })
    if (resume) {
      await waitFor(() => expect(accept).toBeDefined())
      expect(pendingRequest?.signal?.aborted).toBe(false)
      const stale = savedChat('stale', {
        interruptRunId: publicRunId,
        physicalInterruptRunId: physicalRunId,
        interrupts: [{ ...interrupt, id: 'obsolete-approval' }],
      })
      await act(async () => releaseRead?.(Response.json(stale)))
      expect(view.session.getSnapshot().snapshot?.checkpointId).toBe('initial')
      expect(
        view.result.current.runtime.unstable_getPendingInterrupts().map(({ id }) => id),
      ).not.toContain('obsolete-approval')
      await act(async () => accept())
    }
    const streaming = () => thread.getState().messages.at(-1)?.content
    await waitFor(() =>
      expect(streaming()).toContainEqual(
        expect.objectContaining({ type: 'text', text: '새 응답 작성 중' }),
      ),
    )
    if (delayedRead) {
      expect(pendingRequest?.signal?.aborted).toBe(false)
      const post = calls.find(({ method }) => method === 'POST')
      const command = JSON.parse(String(post?.body)) as { runId: string }
      if (resume) expect(command).toMatchObject({ messages: [], resume: [decision] })
      else await act(async () => releaseRead?.(Response.json(savedChat('stale'))))
      expect(view.session.getSnapshot().snapshot?.checkpointId).toBe('initial')
      expect(view.session.getSnapshot().activeRunId).toBe(command.runId)
      expect(streaming()).toContainEqual(
        expect.objectContaining({ type: 'text', text: '새 응답 작성 중' }),
      )
    }
    await act(async () => {
      stored = true
      submitted?.close(terminal)
    })
    await waitFor(() =>
      expect(thread.getState().messages.map(({ id }) => id)).toEqual([
        'question',
        'initial',
        ...(resume ? [] : ['saved-question']),
        'saved-turn',
      ]),
    )
    await waitFor(() => expect(view.onSettled).toHaveBeenCalledTimes(1))
    expect(calls.filter(({ method }) => method === 'POST')).toHaveLength(1)
    expect(calls.filter(({ url }) => url.pathname.endsWith('/stream'))).toHaveLength(0)
    expect(calls.at(-1)?.method).toBe('GET')
  })

  it.each([
    { code: 'AG_UI_READ_DENIED', expectedStreams: 1 },
    { code: 'AG_UI_SNAPSHOT_REQUIRED', expectedStreams: 2 },
  ])(
    'honors the replay failure $code without repeatedly reattaching',
    async ({ code, expectedStreams }) => {
      const hiddenChild = {
        subagentRunId: 'delegated-child',
        parentToolCallId: 'delegate-call',
        name: '검토 담당',
        status: 'running',
      }
      let close!: () => void
      const snapshots = () => calls.filter(({ url }) => url.pathname.endsWith('/snapshot')).length
      const streams = () => calls.filter(({ url }) => url.pathname.endsWith('/stream')).length
      const calls = intercept(({ url }) => {
        if (url.pathname.endsWith('/snapshot'))
          return Response.json(
            savedChat(null, {
              activeRunId: publicRunId,
              physicalRunId,
              subagents: [hiddenChild],
            }),
          )
        if (!url.pathname.endsWith('/stream'))
          throw new Error('Unexpected replay path: ' + url.pathname)
        const output = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              textFrame({
                type: 'RUN_STARTED',
                threadId: conversationId,
                runId: publicRunId,
                protocolVersion: '1.0',
              }),
            )
            close = () => {
              controller.enqueue(
                textFrame({
                  type: 'RUN_ERROR',
                  code,
                  message: 'Reload the authorized chat snapshot.',
                }),
              )
              controller.close()
            }
          },
        })
        return new Response(output, { headers: { 'Content-Type': 'text/event-stream' } })
      })
      const view = mountChat()
      await waitFor(() => expect(streams()).toBe(1))
      await waitFor(() => expect(view.session.getSnapshot().subagents).toHaveLength(1))
      const initialReads = snapshots()
      await act(async () => close())
      if (code === 'AG_UI_READ_DENIED') {
        await waitFor(() => expect(view.session.getSnapshot().readDenied).toBe(true))
        await waitFor(() =>
          expect(view.result.current.runtime.thread.getState().messages).toEqual([]),
        )
        expect(view.session.getSnapshot().subagents).toEqual([])
        expect(view.session.getSnapshot().activeRunId).toBeNull()
        expect(snapshots()).toBe(initialReads)
      } else {
        await waitFor(() => expect(streams()).toBe(2))
        expect(snapshots()).toBeGreaterThan(initialReads)
        await act(async () => close())
        await waitFor(() => expect(view.session.getSnapshot().attachBlockedRunId).toBe(publicRunId))
      }
      // Cover the lifecycle's first reconnect delay even when the failure is delivered before React paints.
      await act(async () => {
        await new Promise<void>((resolve) => setTimeout(resolve, 500))
      })
      expect(streams()).toBe(expectedStreams)
      expect(calls.every(({ method }) => method === 'GET')).toBe(true)
    },
  )

  it('rechecks a visible paused child grant every 15 seconds and drops revoked approval evidence', async () => {
    vi.useFakeTimers({
      toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'],
    })
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    let authorized = true
    const current = childApprovalSnapshot(true)
    const revoked = {
      ...current,
      interruptRunId: null,
      physicalInterruptRunId: null,
      subagents: [],
      interrupts: [],
    }
    const calls = intercept(() => Response.json(authorized ? current : revoked))
    const view = mountChat()
    await advance(0)
    const { runtime } = view.result.current
    const parentAnswer = () => runtime.thread.getState().messages.at(-1)
    expect(parentAnswer()?.content).toContainEqual(
      expect.objectContaining({ type: 'text', text: '저장된 부모 답변' }),
    )
    expect(runtime.unstable_getPendingInterrupts().map(({ id }) => id)).toEqual(['child-slot'])
    expect(view.session.getSnapshot().subagents[0]?.legacyInterruptAccess).toBe(true)
    const reads = calls.length
    const imported = vi.spyOn(runtime.thread, 'import')
    await advance(14_999)
    expect(calls).toHaveLength(reads)
    await advance(1)
    expect(calls).toHaveLength(reads + 1)
    expect(imported).not.toHaveBeenCalled()
    expect(parentAnswer()?.content).toContainEqual(
      expect.objectContaining({ type: 'text', text: '저장된 부모 답변' }),
    )
    authorized = false
    await advance(15_000)
    expect(calls).toHaveLength(reads + 2)
    expect(view.session.getSnapshot().subagents).toEqual([])
    expect(view.session.getSnapshot().interrupts).toEqual([])
    expect(runtime.unstable_getPendingInterrupts()).toEqual([])
    expect(JSON.stringify(runtime.thread.getState().messages)).not.toContain('승인 질문')
    expect(parentAnswer()?.content).toContainEqual(
      expect.objectContaining({ type: 'text', text: '저장된 부모 답변' }),
    )
    await advance(45_000)
    expect(calls).toHaveLength(reads + 2)
    expect(
      calls.every(({ method, url }) => method === 'GET' && url.pathname.endsWith('/snapshot')),
    ).toBe(true)
    expect(view.onSettled).not.toHaveBeenCalled()
    view.unmount()
  })

  it.each(['hidden', 'no-grant', 'unmatched-slot', 'read-only'])(
    'skips periodic reads for %s views',
    async (mode) => {
      vi.useFakeTimers({
        toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'],
      })
      vi.spyOn(document, 'visibilityState', 'get').mockReturnValue(
        mode === 'hidden' ? 'hidden' : 'visible',
      )
      const snapshot =
        mode === 'no-grant'
          ? savedChat('finished')
          : childApprovalSnapshot(mode !== 'unmatched-slot')
      const calls = intercept(() => Response.json(snapshot))
      const view = mountChat(mode === 'read-only')
      await advance(0)
      expect(view.result.current.runtime.thread.getState().messages.at(-1)?.id).toBe(
        snapshot.activeTipMessageId,
      )
      const reads = calls.length
      await advance(45_000)
      expect(calls).toHaveLength(reads)
      expect(
        calls.every(({ method, url }) => method === 'GET' && url.pathname.endsWith('/snapshot')),
      ).toBe(true)
      view.unmount()
    },
  )

  it('reads once more after server run changes during a pending snapshot', async () => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    let respond: () => Response | Promise<Response> = () => Response.json(savedChat('initial'))
    const calls = intercept(() => respond())
    const view = mountChat()
    const head = () => view.result.current.runtime.thread.getState().messages.at(-1)?.id
    await waitFor(() => expect(head()).toBe('initial'))
    const published: Array<string | null | undefined> = []
    const stop = view.session.subscribe(() =>
      published.push(view.session.getSnapshot().snapshot?.checkpointId),
    )
    const initialRequests = calls.length
    let release!: (value: Response) => void
    respond = () =>
      new Promise<Response>((resolve) => {
        release = resolve
      })
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
    })
    await waitFor(() => expect(release).toBeDefined())
    view.rerender({ serverActiveRunId: 'running' })
    view.rerender({ serverActiveRunId: null })
    expect(calls).toHaveLength(initialRequests + 1)
    expect(calls.at(-1)?.signal?.aborted).toBe(false)
    respond = () => Response.json(savedChat('completed'))
    await act(async () => release(Response.json(savedChat('obsolete'))))
    await waitFor(() => expect(head()).toBe('completed'))
    expect(published).toEqual(['completed'])
    expect(calls).toHaveLength(initialRequests + 2)
    expect(
      calls.every(({ method, url }) => method === 'GET' && url.pathname.endsWith('/snapshot')),
    ).toBe(true)
    stop()
  })

  it.each(['pagehide', 'unmount'])(
    'coalesces visibility reads and aborts outstanding branch reads on %s',
    async (departure) => {
      vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
      let release!: () => void
      const held = new Promise<void>((resolve) => {
        release = resolve
      })
      let refreshing = false
      const calls = intercept(() =>
        refreshing
          ? held.then(() => Response.json(savedChat('obsolete')))
          : Response.json(savedChat('initial')),
      )
      const view = mountChat()
      const head = () => view.result.current.runtime.thread.getState().messages.at(-1)?.id
      await waitFor(() => expect(head()).toBe('initial'))
      const initialRequests = calls.length
      refreshing = true
      await act(async () => {
        window.dispatchEvent(new Event('focus'))
      })
      await waitFor(() => expect(calls).toHaveLength(initialRequests + 1))
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
      })
      expect(calls).toHaveLength(initialRequests + 1)
      await act(async () => dispatchMoldyBranchSwitched({ conversationId, checkpointId: 'next' }))
      await waitFor(() => expect(calls).toHaveLength(initialRequests + 2))
      const pending = calls.slice(initialRequests)
      expect(pending.every(({ signal }) => signal?.aborted === false)).toBe(true)
      if (departure === 'pagehide') {
        await act(async () => {
          window.dispatchEvent(new Event('pagehide'))
        })
      } else view.unmount()
      expect(pending.every(({ signal }) => signal?.aborted)).toBe(true)
      await act(async () => release())
      if (departure === 'pagehide') {
        expect(view.session.getSnapshot().snapshot?.checkpointId).toBe('initial')
        expect(head()).toBe('initial')
        view.unmount()
      } else expect(view.session.getSnapshot().snapshot).toBeNull()
      expect(calls).toHaveLength(initialRequests + 2)
      expect(
        pending.every(
          ({ method, url }) =>
            method === 'GET' && !url.search && url.pathname.endsWith('/snapshot'),
        ),
      ).toBe(true)
    },
  )

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
