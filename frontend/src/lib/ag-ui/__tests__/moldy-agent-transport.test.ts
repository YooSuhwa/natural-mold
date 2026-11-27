import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetRefreshBackoff } from '@/lib/api/client'
import { csrfStore } from '@/lib/auth/csrf'
import { beginOrgSwitch, finishOrgSwitch } from '@/lib/org-scope/org-scope-signal'
import { MoldyAgUiAgent } from '../moldy-agent'

const threadId = 'af17af43-5337-41c0-a578-e541b1eca95b'
const publicRunId = 'ddc6e709-37d2-4c6b-971d-42b51006907f'
const physicalRunId = '8172cd72-4d91-4623-b113-5581652ca445'
const nextRunId = 'd1756062-fb43-497d-a9bf-aab3cc472b04'
const base = `/api/conversations/${threadId}/ag-ui`
const message = { id: 'user-1', role: 'user' as const, content: '현재 상태를 알려줘' }

function agentWithMessage() {
  const agent = new MoldyAgUiAgent({ conversationId: threadId })
  agent.messages = [message]
  return agent
}

function snapshot(activeRunId: string | null = null) {
  return {
    threadId,
    protocolVersion: '1.0',
    projectionVersion: '1',
    checkpointId: null,
    activeTipMessageId: null,
    activeRunId,
    physicalRunId: activeRunId ? physicalRunId : null,
    cursor: activeRunId ? 'stored-cursor' : null,
    messages: [],
    messageLinks: [],
    subagents: [],
    activities: [],
    interrupts: [],
  }
}

function begin(runId: string) {
  return { type: 'RUN_STARTED', threadId, runId, protocolVersion: '1.0' }
}

function answer(runId = publicRunId) {
  return [
    begin(runId),
    { type: 'TEXT_MESSAGE_START', messageId: 'assistant-1', role: 'assistant' },
    { type: 'TEXT_MESSAGE_CONTENT', messageId: 'assistant-1', delta: '확인했습니다' },
    { type: 'TEXT_MESSAGE_END', messageId: 'assistant-1' },
    { type: 'RUN_FINISHED', threadId, runId },
  ]
}

function encodeFrames(frames: readonly object[]) {
  return frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join('')
}

function stream(frames: readonly object[], runId = publicRunId) {
  return new Response(encodeFrames(frames), {
    headers: {
      'content-type': 'text/event-stream',
      'X-Run-Id': runId,
      'X-Moldy-Run-Id': physicalRunId,
    },
  })
}

function primaryResponse(runId = publicRunId, workerId?: string) {
  let body!: ReadableStreamDefaultController<Uint8Array>
  const response = new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        body = controller
      },
    }),
    {
      headers: {
        'Content-Type': 'text/event-stream',
        'X-Run-Id': runId,
        ...(workerId
          ? { 'X-Moldy-Run-Id': workerId }
          : {
              'X-Moldy-Input-Id': 'pending-input',
              'X-Moldy-Input-Status': 'pending',
              'X-Moldy-Input-Position': '2',
              'X-Moldy-Input-Revision': '1',
            }),
      },
    },
  )
  const send = (frames: readonly object[]) =>
    body.enqueue(new TextEncoder().encode(encodeFrames(frames)))
  return {
    response,
    start: () => send([begin(runId)]),
    finish: () => {
      if (body.desiredSize !== null) {
        send(answer(runId).slice(1))
        body.close()
      }
    },
  }
}

type RequestLog = { url: URL; init: RequestInit; method: string; headers: Headers }

function intercept(reply: (request: RequestLog, index: number) => Response | Promise<Response>) {
  const calls: RequestLog[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input, init = {}) => {
      const url = new URL(input instanceof Request ? input.url : input.toString())
      const request = {
        url,
        init,
        method: (init.method ?? 'GET').toUpperCase(),
        headers: new Headers(init.headers),
      }
      calls.push(request)
      return reply(request, calls.length)
    }),
  )
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
  csrfStore.clear()
  resetRefreshBackoff()
})

describe('MoldyAgUiAgent HTTP contract', () => {
  it('streams a new user message through the official agent with cookie and CSRF auth', async () => {
    csrfStore.set('test-csrf')
    const calls = intercept(() => stream(answer()))
    const agent = agentWithMessage()
    const result = await agent.runAgent({ runId: publicRunId })

    expect(result.newMessages).toEqual([
      expect.objectContaining({ id: 'assistant-1', content: '확인했습니다' }),
    ])
    expect(calls).toHaveLength(1)
    const [request] = calls
    expect(request.url.pathname).toBe(`${base}/runs`)
    expect(request.method).toBe('POST')
    expect(request.init.credentials).toBe('include')
    expect(request.headers.get('X-CSRF-Token')).toBe('test-csrf')
    expect(JSON.parse(String(request.init.body))).toMatchObject({
      protocolVersion: '1.0',
      runId: publicRunId,
      threadId,
      messages: [message],
    })
  })

  it('sends only the new message and the selected Moldy attachment references', async () => {
    const calls = intercept(() => stream(answer()))
    const attachmentId = 'c5b41bc3-657e-4bfe-82dc-bc83c673c68c'
    const agent = agentWithMessage()
    agent.messages = [
      { id: 'history-system', role: 'system', content: 'ignored history' },
      { id: 'history-reply', role: 'assistant', content: 'earlier reply' },
      message,
    ]
    agent.state = { privateValue: 'ignored' }
    await agent.runAgent({
      runId: publicRunId,
      context: [{ description: 'system', value: 'ignored client context' }],
      forwardedProps: {
        unrelated: 'ignored',
        runConfig: { moldy: { attachmentIds: [attachmentId] } },
      },
      tools: [{ name: 'client-chosen', description: 'ignored', parameters: { type: 'object' } }],
    })
    const body = JSON.parse(String(calls[0].init.body))
    expect(body).toMatchObject({
      messages: [message],
      state: {},
      context: [],
      tools: [],
      forwardedProps: { moldy: { attachmentIds: [attachmentId] } },
    })
    expect(JSON.stringify(body)).not.toMatch(/ignored|history-system|history-reply|client-chosen/i)
  })

  it('projects an uploaded document and a version-pinned resource reference without extra metadata', async () => {
    const calls = intercept(() => stream(answer()))
    const agent = agentWithMessage()
    const upload = 'c5b41bc3-657e-4bfe-82dc-bc83c673c68c'
    const reference = {
      kind: 'artifact',
      id: physicalRunId,
      version_id: nextRunId,
      label: '참조 파일',
    }
    const part = {
      type: 'document' as const,
      source: { type: 'file' as const, value: upload, mimeType: 'application/pdf' },
      metadata: { privateNote: 'test-only note' },
    }
    agent.messages = [{ id: 'attachment-only', role: 'user', content: [part] }]
    await agent.runAgent({
      runId: publicRunId,
      forwardedProps: { runConfig: { resource_context: [reference] } },
    })
    const body = JSON.parse(String(calls[0].init.body))
    expect(body.forwardedProps).toEqual({
      moldy: { attachmentIds: [upload], resourceContext: { resources: [reference] } },
    })
    expect(body.messages).toEqual([
      {
        id: 'attachment-only',
        role: 'user',
        content: [
          {
            type: 'document',
            source: { type: 'file', value: upload, provider: 'moldy', mimeType: 'application/pdf' },
          },
        ],
      },
    ])
  })

  it('rejects external image URLs and inconsistent local upload references before fetch', async () => {
    const calls = intercept(() => stream(answer()))
    const agent = agentWithMessage()
    agent.messages = [
      {
        id: 'external',
        role: 'user',
        content: [
          { type: 'image', source: { type: 'url', value: 'https://example.invalid/ignored.png' } },
        ],
      },
    ]
    await expect(agent.runAgent({ runId: publicRunId })).rejects.toThrow()
    agent.messages = [
      {
        id: 'mismatch',
        role: 'user',
        content: [{ type: 'image', source: { type: 'file', value: threadId } }],
      },
    ]
    await expect(
      agent.runAgent({
        runId: nextRunId,
        forwardedProps: { moldy: { attachmentIds: [physicalRunId] } },
      }),
    ).rejects.toThrow()
    await expect(
      agent.runAgent({
        runId: nextRunId,
        forwardedProps: {
          runConfig: { resource_context: [{ kind: 'artifact', id: threadId, bytes: 'invalid' }] },
        },
      }),
    ).rejects.toThrow()
    expect(calls).toHaveLength(0)
  })

  it('rejects a tool-only command before sending it to the server', async () => {
    const calls = intercept(() => stream(answer()))
    const agent = agentWithMessage()
    agent.messages = [{ role: 'tool', id: 'result', toolCallId: 'unissued', content: 'ignored' }]
    await expect(agent.runAgent({ runId: publicRunId })).rejects.toThrow()
    expect(calls).toHaveLength(0)
  })

  it('regenerates from its requested checkpoint without reposting any history or system instructions', async () => {
    const calls = intercept(() => stream(answer()))
    const agent = agentWithMessage()
    agent.messages.push({ role: 'assistant', id: 'old-answer', content: '답변 이력' })
    await agent.runAgent({
      runId: publicRunId,
      forwardedProps: {
        runConfig: {
          moldy: {
            operation: 'regenerate',
            checkpointId: 'branch-checkpoint',
            expectedHead: 'source-head',
          },
        },
      },
    })
    expect(JSON.parse(String(calls[0].init.body))).toMatchObject({
      messages: [],
      forwardedProps: {
        moldy: {
          operation: 'regenerate',
          checkpointId: 'branch-checkpoint',
          expectedHead: 'source-head',
        },
      },
    })
  })

  it('replays the identical command with a renewed CSRF token following a 401', async () => {
    csrfStore.set('expired')
    const calls = intercept(({ url }, index) => {
      if (url.pathname === '/api/auth/refresh') return Response.json({ csrf_token: 'renewed' })
      if (index === 1) return new Response(null, { status: 401 })
      return stream(answer())
    })
    const result = await agentWithMessage().runAgent({ runId: publicRunId })

    expect(result.newMessages).toHaveLength(1)
    expect(calls.map(({ url }) => url.pathname)).toEqual([
      `${base}/runs`,
      '/api/auth/refresh',
      `${base}/runs`,
    ])
    expect(calls[0].init.body).toBe(calls[2].init.body)
    expect(calls[0].headers.get('X-CSRF-Token')).toBe('expired')
    expect(calls[2].headers.get('X-CSRF-Token')).toBe('renewed')
  })

  it('recovers one lost acknowledgement using the same command ID and body', async () => {
    const calls = intercept((_, index) => {
      if (index === 1) throw new TypeError('acknowledgement lost')
      return stream(answer())
    })
    const onBeforeSubmit = vi.fn()
    const onAccepted = vi.fn()
    const agent = new MoldyAgUiAgent({ conversationId: threadId, onBeforeSubmit, onAccepted })
    agent.messages = [message]
    await agent.runAgent({ runId: publicRunId })
    expect(onBeforeSubmit).toHaveBeenCalledOnce()
    expect(onAccepted).toHaveBeenCalledOnce()
    expect(calls.map((call) => [call.method, call.url.pathname])).toEqual([
      ['POST', `${base}/runs`],
      ['POST', `${base}/runs`],
    ])
    expect(calls[0].init.body).toBe(calls[1].init.body)
  })

  it('propagates a persistent pre-header failure after a single retry', async () => {
    const calls = intercept(() => {
      throw new TypeError('connection offline')
    })
    await expect(agentWithMessage().runAgent({ runId: publicRunId })).rejects.toThrow()
    expect(calls).toHaveLength(2)
    expect(calls[0].init.body).toBe(calls[1].init.body)
  })

  it('does not reissue POST when an SSE response fails after yielding visible text', async () => {
    let body!: ReadableStreamDefaultController<Uint8Array>
    const source = new ReadableStream<Uint8Array>({
      start(controller) {
        body = controller
        controller.enqueue(new TextEncoder().encode(encodeFrames(answer().slice(0, 3))))
      },
    })
    const calls = intercept(({ method, url }) => {
      if (method === 'GET') {
        if (url.pathname === `${base}/snapshot`) return Response.json(snapshot(publicRunId))
        return stream(answer())
      }
      return new Response(source, {
        headers: {
          'content-type': 'text/event-stream',
          'X-Run-Id': publicRunId,
          'X-Moldy-Run-Id': physicalRunId,
        },
      })
    })
    const observed = vi.fn(() => body.error(new TypeError('SSE interrupted')))
    const agent = agentWithMessage()
    await expect(
      agent.runAgent({ runId: publicRunId }, { onTextMessageContentEvent: observed }),
    ).rejects.toThrow()
    expect(observed).toHaveBeenCalledOnce()
    expect(calls).toHaveLength(1)
    const reopened = agentWithMessage()
    const result = await reopened.connectAgent()
    expect(result.newMessages).toEqual([
      expect.objectContaining({ id: 'assistant-1', content: '확인했습니다' }),
    ])
    expect(calls.map(({ method, url }) => [method, url.pathname])).toEqual([
      ['POST', `${base}/runs`],
      ['GET', `${base}/snapshot`],
      ['GET', `${base}/stream`],
    ])
    expect(calls[2].url.searchParams.get('runId')).toBe(publicRunId)
  })

  it('addresses the current interrupt on a new run exactly once', async () => {
    const approvalId = 'credential-request-slot-2'
    const calls = intercept((_, index) =>
      index === 1
        ? stream([
            begin(publicRunId),
            {
              type: 'RUN_FINISHED',
              threadId,
              runId: publicRunId,
              outcome: {
                type: 'interrupt',
                interrupts: [{ id: approvalId, reason: 'credential_required' }],
              },
            },
          ])
        : stream(answer(nextRunId), nextRunId),
    )
    const agent = agentWithMessage()
    await agent.runAgent({ runId: publicRunId })
    expect(agent.pendingInterrupts.map(({ id }) => id)).toEqual([approvalId])
    await expect(agent.runAgent({ runId: nextRunId })).rejects.toThrow()
    expect(calls).toHaveLength(1)
    const resume = [
      { interruptId: approvalId, status: 'resolved' as const, payload: { decision: 'retry' } },
    ]
    await agent.runAgent({
      runId: nextRunId,
      resume,
      forwardedProps: { runConfig: { moldy: { attachRunId: publicRunId } } },
    })
    expect(calls).toHaveLength(2)
    expect(JSON.parse(String(calls[1].init.body))).toMatchObject({
      threadId,
      runId: nextRunId,
      messages: [],
      forwardedProps: {},
      resume,
    })
  })

  it('connects fresh SDK instances only to the server-selected run, starting from its first frame', async () => {
    const publicId = 'job/issued?by=server'
    const calls = intercept(({ url, method, headers }) => {
      expect(method).toBe('GET')
      expect(headers.get('Last-Event-ID')).toBeNull()
      if (url.pathname === `${base}/snapshot`) return Response.json(snapshot(publicId))
      expect(url.pathname).toBe(`${base}/stream`)
      expect([...url.searchParams]).toEqual([['runId', publicId]])
      return stream(answer(publicId), publicId)
    })
    const original = agentWithMessage()
    await original.readSnapshot({})
    const clone = original.clone()
    const results = await Promise.all([
      original.connectAgent({ runId: 'ui-temporary' }),
      clone.connectAgent({ runId: 'untrusted-cursor' }),
    ])
    for (const result of results)
      expect(result.newMessages).toEqual([expect.objectContaining({ id: 'assistant-1' })])
    expect(calls.filter(({ url }) => url.pathname === `${base}/stream`)).toHaveLength(2)
    expect(calls.every(({ method }) => method === 'GET')).toBe(true)
  })

  it('detaches a live clone when its conversation is disposed without cancelling the worker', async () => {
    const calls = intercept(({ url, init }) => {
      if (url.pathname === `${base}/snapshot`) return Response.json(snapshot(publicRunId))
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(encodeFrames([begin(publicRunId)])))
            init.signal?.addEventListener(
              'abort',
              () => controller.error(new DOMException('detached', 'AbortError')),
              { once: true },
            )
          },
        }),
        {
          headers: {
            'content-type': 'text/event-stream',
            'X-Run-Id': publicRunId,
            'X-Moldy-Run-Id': physicalRunId,
          },
        },
      )
    })
    const agent = agentWithMessage()
    await agent.readSnapshot({})
    const clone = agent.clone()
    const started = vi.fn()
    const result = clone
      .connectAgent({ runId: 'local-id' }, { onRunStartedEvent: started })
      .catch(() => undefined)
    try {
      await vi.waitFor(() => expect(started).toHaveBeenCalledOnce())
      agent.dispose()
      await result
      const attach = calls.find((call) => call.url.pathname === `${base}/stream`)
      expect(attach?.init.signal?.aborted).toBe(true)
      expect(calls.every(({ method }) => method === 'GET')).toBe(true)
    } finally {
      agent.dispose()
    }
  })

  it.each([
    ['POST', 'runId'],
    ['POST', 'threadId'],
    ['GET', 'runId'],
    ['GET', 'threadId'],
  ] as const)(
    'rejects a different %s response %s before any frames reach the UI',
    async (method, field) => {
      const observer = vi.fn()
      const calls = intercept(({ url }) => {
        if (url.pathname === `${base}/snapshot`) return Response.json(snapshot(publicRunId))
        const frames = answer().map((frame) =>
          field in frame ? { ...frame, [field]: nextRunId } : frame,
        )
        return stream(frames, publicRunId)
      })
      const agent = new MoldyAgUiAgent({ conversationId: threadId, onPublicEvent: observer })
      agent.messages = [message]
      const result =
        method === 'POST'
          ? agent.runAgent({ runId: publicRunId })
          : agent.connectAgent({ runId: publicRunId })
      await expect(result).rejects.toThrow()
      expect(observer).not.toHaveBeenCalled()
      expect(agent.messages).toEqual([message])
      expect(calls.filter((call) => call.url.pathname !== `${base}/snapshot`)).toHaveLength(1)
    },
  )

  it.each(['run', 'attach'] as const)(
    'correlates a standalone error with its public %s source',
    async (source) => {
      const observer = vi.fn()
      const calls = intercept(({ url }) =>
        url.pathname === `${base}/snapshot`
          ? Response.json(snapshot(publicRunId))
          : stream([
              { type: 'RUN_ERROR', message: 'The run could not be started.', code: 'REJECTED' },
            ]),
      )
      const agent = new MoldyAgUiAgent({ conversationId: threadId, onPublicEvent: observer })
      agent.messages = [message]
      const result =
        source === 'run'
          ? agent.runAgent({ runId: publicRunId })
          : agent.connectAgent({ runId: 'generated-by-sdk' })
      await result.catch(() => undefined)
      expect(observer).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'RUN_ERROR', code: 'REJECTED' }),
        { source, runId: publicRunId },
      )
      expect(calls.map(({ method }) => method)).toEqual(
        source === 'run' ? ['POST'] : ['GET', 'GET'],
      )
    },
  )

  it('declines a stale reconnect when the public selection changed', async () => {
    const calls = intercept(() => Response.json(snapshot(nextRunId)))
    const agent = agentWithMessage()
    await agent.readSnapshot()
    await agent.connectAgent({
      forwardedProps: { runConfig: { moldy: { attachRunId: publicRunId } } },
    })
    expect(calls.map(({ method, url }) => [method, url.pathname])).toEqual([
      ['GET', `${base}/snapshot`],
    ])
    expect(agent.messages).toEqual([message])
  })

  it('lets a superseded snapshot finish without replacing the selected run or approvals', async () => {
    const delayed = Promise.withResolvers<Response>()
    const calls = intercept(({ url }, index) => {
      if (url.pathname === `${base}/stream`) return stream(answer())
      return index === 1 ? Response.json(snapshot(publicRunId)) : delayed.promise
    })
    const agent = agentWithMessage()
    await agent.readSnapshot()
    const approvals = [{ id: 'current-approval', reason: 'credential_required' }]
    agent.pendingInterrupts = approvals
    let isCurrent = true
    const pending = agent.readSnapshot({ isCurrent: () => isCurrent })
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    isCurrent = false
    delayed.resolve(Response.json(snapshot(nextRunId)))
    expect((await pending).activeRunId).toBe(nextRunId)
    expect(calls[1].init.signal?.aborted).toBe(false)
    expect(agent.pendingInterrupts).toEqual(approvals)

    await agent.connectAgent({
      forwardedProps: { runConfig: { moldy: { attachRunId: publicRunId } } },
    })
    expect(
      calls.map(({ method, url }) => [method, url.pathname, url.searchParams.get('runId')]),
    ).toEqual([
      ['GET', `${base}/snapshot`, null],
      ['GET', `${base}/snapshot`, null],
      ['GET', `${base}/stream`, publicRunId],
    ])
  })

  it('does not create a run when reopening an idle thread', async () => {
    const calls = intercept(() => Response.json(snapshot()))
    await agentWithMessage().connectAgent({ runId: 'generated-by-sdk' })
    expect(calls.map(({ method, url }) => [method, url.pathname])).toEqual([
      ['GET', `${base}/snapshot`],
    ])
  })

  it('aborts requests issued by the former organization without retrying or sending cancel', async () => {
    const calls = intercept(
      ({ init }) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener(
            'abort',
            () => reject(new TypeError('changed organization')),
            { once: true },
          )
        }),
    )
    const pending = agentWithMessage().runAgent({ runId: publicRunId })
    try {
      await vi.waitFor(() => expect(calls).toHaveLength(1))
      beginOrgSwitch()
      expect((await pending).newMessages).toEqual([])
      expect(calls[0].init.signal?.aborted).toBe(true)
      expect(calls).toHaveLength(1)
    } finally {
      finishOrgSwitch()
    }
  })

  it.each([false, true])(
    'publishes one pinned claim after a primary run starts (preceding worker read: %s)',
    async (readPrecedingWorker) => {
      const pending = primaryResponse()
      const calls = intercept(({ method, url }) => {
        if (method === 'POST') return pending.response
        if (!url.searchParams.has('runId')) return Response.json(snapshot(nextRunId))
        return Response.json({ ...snapshot(publicRunId), runId: publicRunId })
      })
      const onAccepted = vi.fn()
      const onRunClaimed = vi.fn()
      const onPublicEvent = vi.fn()
      const agent = new MoldyAgUiAgent({
        conversationId: threadId,
        onAccepted,
        onRunClaimed,
        onPublicEvent,
      })
      agent.messages = [message]
      const result = agent.runAgent({ runId: publicRunId })
      await vi.waitFor(() => expect(onAccepted).toHaveBeenCalledOnce())
      expect(onAccepted).toHaveBeenCalledWith(
        expect.objectContaining({ publicRunId, physicalRunId: null }),
      )
      expect(onRunClaimed).not.toHaveBeenCalled()
      expect(onPublicEvent).not.toHaveBeenCalled()
      expect(calls).toHaveLength(1)

      if (readPrecedingWorker) {
        expect((await agent.readSnapshot()).activeRunId).toBe(nextRunId)
        expect(onRunClaimed).not.toHaveBeenCalled()
      }
      pending.start()
      await vi.waitFor(() => expect(onRunClaimed).toHaveBeenCalledOnce())
      expect(onRunClaimed).toHaveBeenCalledWith({ publicRunId, physicalRunId })
      pending.finish()
      await result
      expect(calls.map(({ method, url }) => [method, url.pathname, [...url.searchParams]])).toEqual(
        [
          ['POST', `${base}/runs`, []],
          ...(readPrecedingWorker ? [['GET', `${base}/snapshot`, []]] : []),
          ['GET', `${base}/snapshot`, [['runId', publicRunId]]],
        ],
      )
      expect(onAccepted).toHaveBeenCalledOnce()
      expect(onRunClaimed).toHaveBeenCalledOnce()
      expect(onPublicEvent).toHaveBeenCalledTimes(answer().length)
      for (const frame of answer()) {
        expect(onPublicEvent).toHaveBeenCalledWith(expect.objectContaining(frame), {
          source: 'run',
          runId: publicRunId,
        })
      }
    },
  )

  it.each([
    ['another snapshot', { runId: nextRunId }],
    ['another active run', { activeRunId: nextRunId }],
    ['an idle conversation', { activeRunId: null }],
    ['a worker that has not claimed the run', { physicalRunId: null }],
  ] as const)('ignores a queued claim snapshot for %s', async (_label, fields) => {
    const pending = primaryResponse()
    const calls = intercept(({ method }) =>
      method === 'POST'
        ? pending.response
        : Response.json({ ...snapshot(publicRunId), runId: publicRunId, ...fields }),
    )
    const onRunClaimed = vi.fn()
    const agent = new MoldyAgUiAgent({ conversationId: threadId, onRunClaimed })
    agent.messages = [message]
    const result = agent.runAgent({ runId: publicRunId })
    pending.start()
    await vi.waitFor(() => expect(calls.map(({ method }) => method)).toEqual(['POST', 'GET']))
    pending.finish()
    await result
    expect(onRunClaimed).not.toHaveBeenCalled()
  })

  it('ignores an old primary start after the next primary receipt has arrived', async () => {
    const pending = primaryResponse()
    const replacement = primaryResponse(nextRunId, 'worker-2')
    const calls = intercept(({ method }, index) => {
      if (method === 'GET') return Response.json({ ...snapshot(publicRunId), runId: publicRunId })
      return index === 1 ? pending.response : replacement.response
    })
    const onAccepted = vi.fn()
    const onRunClaimed = vi.fn()
    const agent = new MoldyAgUiAgent({ conversationId: threadId, onAccepted, onRunClaimed })
    agent.messages = [message]
    const oldStarted = vi.fn()
    const oldResult = agent
      .clone()
      .runAgent({ runId: publicRunId }, { onRunStartedEvent: oldStarted })
    await vi.waitFor(() => expect(onAccepted).toHaveBeenCalledOnce())
    const replacementResult = agent.runAgent({ runId: nextRunId })
    await vi.waitFor(() =>
      expect(onAccepted).toHaveBeenCalledWith(
        expect.objectContaining({
          publicRunId: nextRunId,
          physicalRunId: 'worker-2',
        }),
      ),
    )

    pending.start()
    await vi.waitFor(() => expect(oldStarted).toHaveBeenCalledOnce())
    expect(calls.map(({ method }) => method)).toEqual(['POST', 'POST'])
    pending.finish()
    await oldResult
    replacement.start()
    replacement.finish()
    await replacementResult
    expect(onRunClaimed).not.toHaveBeenCalled()
    expect(calls.map(({ method }) => method)).toEqual(['POST', 'POST'])
  })

  it.each(['disposal', 'terminal event', 'a newer run'] as const)(
    'ignores a delayed worker claim after %s',
    async (transition) => {
      const pending = primaryResponse()
      const replacement = primaryResponse(nextRunId, 'worker-2')
      const delayedSnapshot = Promise.withResolvers<Response>()
      const calls = intercept(({ method }, index) => {
        if (method === 'GET') return delayedSnapshot.promise
        if (index === 1) return pending.response
        return replacement.response
      })
      const onRunClaimed = vi.fn()
      const onPublicEvent = vi.fn()
      const agent = new MoldyAgUiAgent({ conversationId: threadId, onRunClaimed, onPublicEvent })
      agent.messages = [message]
      const reader = agent.clone()
      const result = reader.runAgent({ runId: publicRunId }).catch((error: unknown) => {
        if (transition !== 'disposal') throw error
      })
      pending.start()
      await vi.waitFor(() => expect(calls[1]?.url.searchParams.get('runId')).toBe(publicRunId))
      if (transition === 'disposal') agent.dispose()
      if (transition === 'terminal event') {
        pending.finish()
        await result
      }
      const replacementResult =
        transition === 'a newer run' ? agent.runAgent({ runId: nextRunId }) : null
      if (replacementResult) {
        replacement.start()
        await vi.waitFor(() =>
          expect(onPublicEvent).toHaveBeenCalledWith(expect.objectContaining(begin(nextRunId)), {
            source: 'run',
            runId: nextRunId,
          }),
        )
        onPublicEvent.mockClear()
      }

      delayedSnapshot.resolve(Response.json({ ...snapshot(publicRunId), runId: publicRunId }))
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(onRunClaimed).not.toHaveBeenCalled()
      if (transition !== 'terminal event') {
        pending.finish()
        await result
      }
      if (replacementResult) {
        // Any remaining frames from the old clone retain their original public ID.
        for (const [, context] of onPublicEvent.mock.calls) {
          expect(context).toEqual({ source: 'run', runId: publicRunId })
        }
        replacement.finish()
        await replacementResult
      }
    },
  )

  it('returns a pending queue receipt without creating a placeholder answer or cancelling a run', async () => {
    const closeStream = vi.fn()
    const calls = intercept(
      () =>
        new Response(new ReadableStream({ cancel: closeStream }), {
          headers: {
            'content-type': 'text/event-stream',
            'X-Run-Id': publicRunId,
            'X-Moldy-Input-Id': 'input-1',
            'X-Moldy-Input-Status': 'pending',
            'X-Moldy-Input-Revision': '1',
            'X-Moldy-Input-Position': '2',
          },
        }),
    )
    const onAccepted = vi.fn()
    const onBeforeSubmit = vi.fn()
    const agent = new MoldyAgUiAgent({ conversationId: threadId, onBeforeSubmit, onAccepted })
    agent.messages = [message]
    const receipt = await agent.submitQueuedInput(
      { id: 'message-2', text: '다음 질문', attachmentIds: [] },
      'enqueue',
      publicRunId,
    )
    expect(receipt).toMatchObject({
      inputId: 'input-1',
      inputStatus: 'pending',
      position: 2,
      revision: 1,
    })
    expect(agent.messages).toEqual([message])
    expect(closeStream).toHaveBeenCalledOnce()
    expect(onBeforeSubmit).not.toHaveBeenCalled()
    expect(onAccepted).not.toHaveBeenCalled()
    expect(calls.map(({ method, url }) => [method, url.pathname])).toEqual([
      ['POST', `${base}/runs`],
    ])
  })
})
