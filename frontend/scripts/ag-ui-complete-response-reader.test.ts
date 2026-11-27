import { EventType } from '@ag-ui/core'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { observeCompletedAgUiResponseReads } from '../e2e/helpers/ag-ui-complete-response-reader'

const origin = 'http://localhost:8101'
const threadId = 'thread-1'
const runId = 'run-1'
const physicalRunId = 'worker-1'
const url = origin + '/api/conversations/' + threadId + '/ag-ui/runs'
const binding = '__testCompletedRead'
const secret = 'local-test'
const start = { type: 'RUN_STARTED', protocolVersion: '1.0', threadId, runId }
const finish = { type: 'RUN_FINISHED', threadId, runId, outcome: { type: 'success' } }

const cancelled = { ...finish, outcome: { type: 'cancelled' } }

function frame(event: unknown) {
  return 'id: resume-cursor\r\ndata: ' + JSON.stringify(event) + '\r\n\r\n'
}

function harness(requestUrl = url, receipt: Record<string, string> = {}) {
  const reports = vi.fn<(signal: { kind: string }) => Promise<void>>().mockResolvedValue(undefined)
  const encoder = new TextEncoder()
  const controller = new AbortController()
  let source!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({
    start(streamController) {
      source = streamController
    },
  })
  const response = new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'X-Run-Id': runId,
      'X-Moldy-Run-Id': physicalRunId,
      ...receipt,
    },
  })
  Object.defineProperty(response, 'url', { value: requestUrl })
  const nativeFetch = vi.fn<typeof fetch>().mockResolvedValue(response)
  vi.stubGlobal('fetch', nativeFetch)
  vi.stubGlobal(binding, reports)
  observeCompletedAgUiResponseReads({
    origin,
    binding,
    secret,
    eventTypes: Object.values(EventType),
  })

  const result = window.fetch(requestUrl, {
    method: 'POST',
    credentials: 'include',
    signal: controller.signal,
    body: JSON.stringify({ threadId, runId, protocolVersion: '1.0' }),
  })
  const reader = async () => {
    const { body } = await result
    if (!body) throw new Error('Test response must contain a readable stream.')
    return body.getReader()
  }
  const readAll = async () => {
    const applicationReader = await reader()
    while (!(await applicationReader.read()).done) {
      // The application consumes the original stream through its ordinary reader.
    }
    applicationReader.releaseLock()
  }
  // JSDOM's TextEncoder runs in Node, while browser stream chunks belong to the window realm.
  const write = (text: string) => source.enqueue(new Uint8Array(encoder.encode(text)))
  const reportsOf = (kind: string) =>
    reports.mock.calls.flat().filter((signal) => signal.kind === kind)
  return { response, nativeFetch, result, reader, readAll, source, controller, write, reportsOf }
}

afterEach(() => {
  window.dispatchEvent(new Event('pagehide'))
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('native application reads of AG-UI command responses', () => {
  it.each([
    { outcome: finish.outcome, expectedKind: 'eof' },
    {
      outcome: { type: 'interrupt', interrupts: [{ id: 'choice-1', reason: '사용자 승인' }] },
      expectedKind: 'eof',
    },
    { outcome: cancelled.outcome, expectedKind: 'cancelled-eof' },
  ])(
    'reports $expectedKind for $outcome.type only after the native reader returns EOF',
    async ({ outcome, expectedKind }) => {
      const h = harness()
      const originalRead = vi.spyOn(ReadableStreamDefaultReader.prototype, 'read')
      const bytes = new TextEncoder().encode(
        ': keepalive\r\n\r\n' + frame(start) + frame({ ...finish, outcome }),
      )
      // Single-byte chunks exercise CR/LF, JSON, and a multibyte UTF-8 code point across reads.
      for (const byte of bytes) h.source.enqueue(Uint8Array.of(byte))
      h.source.close()
      expect(h.result).toBe(h.nativeFetch.mock.results[0].value)
      expect(await h.result).toBe(h.response)
      const reader = await h.reader()
      expect(originalRead).not.toHaveBeenCalled()
      for (const byte of bytes) {
        const pending = reader.read()
        expect(pending).toBe(originalRead.mock.results.at(-1)?.value)
        expect(await pending).toEqual({ done: false, value: Uint8Array.of(byte) })
      }
      expect(h.reportsOf('eof')).toEqual([])
      expect(h.reportsOf('cancelled-eof')).toEqual([])
      expect(await reader.read()).toEqual({ done: true, value: undefined })
      reader.releaseLock()
      expect(h.reportsOf(expectedKind)).toEqual([
        {
          kind: expectedKind,
          document: expect.any(String),
          secret,
          threadId,
          runId,
          physicalRunId,
        },
      ])
      expect(h.reportsOf(expectedKind === 'eof' ? 'cancelled-eof' : 'eof')).toEqual([])
      h.controller.abort()
      expect(h.reportsOf('invalid')).toEqual([])
    },
  )

  it.each([
    ['missing start', [finish]],
    ['missing terminal', [start]],
    ['different initial thread', [{ ...start, threadId: 'other' }, finish]],
    ['different terminal run', [start, { ...finish, runId: 'other' }]],
    ['missing outcome', [start, { ...finish, outcome: undefined }]],
    ['invalid usage', [start, { ...finish, usage: [{ inputTokens: '12' }] }]],
    ['null rawEvent', [start, { ...finish, rawEvent: null }]],
    ['null result', [start, { ...finish, result: null }]],
    [
      'empty interrupt ID',
      [
        start,
        { ...finish, outcome: { type: 'interrupt', interrupts: [{ id: '', reason: 'pause' }] } },
      ],
    ],
    [
      'malformed interrupts',
      [start, { ...finish, outcome: { type: 'interrupt', interrupts: {} } }],
    ],
    ['run error', [start, { type: 'RUN_ERROR', message: 'failed' }]],
    ['cancel with wrong thread', [start, { ...cancelled, threadId: 'other' }]],
    ['cancel with wrong run', [start, { ...cancelled, runId: 'other' }]],
    [
      'cancel with extra outcome fields',
      [start, { ...cancelled, outcome: { type: 'cancelled', reason: 'unexpected' } }],
    ],
    ['duplicate start', [start, start, finish]],
    ['duplicate terminal', [start, finish, finish]],
    [
      'unexpected data after terminal',
      [start, finish, { type: 'STEP_STARTED', stepName: 'extra' }],
    ],
  ] as const)(
    'rejects %s even when the application reads clean EOF',
    async (_description, events) => {
      const h = harness()
      h.write(events.map(frame).join(''))
      h.source.close()
      await h.readAll()
      expect(h.reportsOf('eof')).toEqual([])
      expect(h.reportsOf('cancelled-eof')).toEqual([])
    },
  )

  it.each(['invalid UTF-8', 'unterminated SSE'])('fails closed for %s at EOF', async (scenario) => {
    const h = harness()
    if (scenario === 'invalid UTF-8') {
      h.write(frame(start) + frame(finish))
      h.source.enqueue(Uint8Array.of(0xff))
    } else {
      h.write(frame(start) + frame(finish).trimEnd())
    }
    h.source.close()
    await h.readAll()
    expect(h.reportsOf('eof')).toEqual([])
  })

  it.each(['request abort', 'reader cancel', 'read rejection', 'early release'])(
    'does not promote a terminal read followed by %s',
    async (scenario) => {
      const h = harness()
      h.write(frame(start) + frame(finish))
      const reader = await h.reader()
      expect((await reader.read()).done).toBe(false)
      if (scenario === 'request abort') {
        h.controller.abort()
        h.source.close()
        expect((await reader.read()).done).toBe(true)
      } else if (scenario === 'reader cancel') {
        await reader.cancel()
        expect((await reader.read()).done).toBe(true)
      } else if (scenario === 'read rejection') {
        const failure = new Error('stream disconnected')
        h.source.error(failure)
        await expect(reader.read()).rejects.toBe(failure)
      } else {
        reader.releaseLock()
        h.source.close()
        const replacement = await h.reader()
        expect((await replacement.read()).done).toBe(true)
      }
      expect(h.reportsOf('eof')).toEqual([])
      expect(h.reportsOf('cancelled-eof')).toEqual([])
    },
  )

  it.each(['reader cancel', 'request abort', 'read rejection'])(
    'denies cancelled completion when %s precedes EOF',
    async (operation) => {
      const h = harness()
      h.write(frame(start) + frame(cancelled))
      const reader = await h.reader()
      expect((await reader.read()).done).toBe(false)
      if (operation === 'read rejection') {
        const failure = new Error('stream disconnected after terminal')
        h.source.error(failure)
        await expect(reader.read()).rejects.toBe(failure)
      } else {
        if (operation === 'reader cancel') await reader.cancel()
        else {
          h.controller.abort()
          h.source.close()
        }
        expect((await reader.read()).done).toBe(true)
      }
      expect(h.reportsOf('eof')).toEqual([])
      expect(h.reportsOf('cancelled-eof')).toEqual([])
    },
  )

  it('refuses an oversized unterminated frame while preserving the original bytes', async () => {
    const h = harness()
    const reader = await h.reader()
    const oversized = new Uint8Array(1024 * 1024 + 1)
    h.source.enqueue(oversized)
    expect((await reader.read()).value).toBe(oversized)
    h.write(frame(start) + frame(finish))
    h.source.close()
    expect((await reader.read()).done).toBe(false)
    expect((await reader.read()).done).toBe(true)
    expect(h.reportsOf('eof')).toEqual([])
  })

  it.each([
    ['foreign API', 'http://localhost:9999/api/conversations/' + threadId + '/ag-ui/runs', {}],
    ['snapshot route', origin + '/api/conversations/' + threadId + '/ag-ui/snapshot', {}],
    ['wrong public receipt', url, { 'X-Run-Id': 'other' }],
    ['missing physical receipt', url, { 'X-Moldy-Run-Id': '' }],
  ] as const)('ignores a completed reader for %s', async (_label, path, headers) => {
    const h = harness(path, headers)
    h.write(frame(start) + frame(finish))
    h.source.close()
    await h.readAll()
    expect(h.reportsOf('eof')).toEqual([])
  })
})
