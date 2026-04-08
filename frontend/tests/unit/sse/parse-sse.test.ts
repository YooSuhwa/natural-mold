import { describe, it, expect, vi, beforeEach } from 'vitest'
import { parseSSEStream, streamSSEPost } from '@/lib/sse/parse-sse'

const API_BASE = 'http://localhost:8001'

/**
 * Helper: ReadableStream을 SSE 형식 텍스트로 생성한다.
 */
function createSSEStream(lines: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  const text = lines.join('\n') + '\n'
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text))
      controller.close()
    },
  })
}

/**
 * Helper: 청크 단위로 분할된 ReadableStream을 생성한다.
 */
function createChunkedStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })
}

/**
 * Helper: parseSSEStream 제너레이터의 이벤트를 모두 수집한다.
 */
async function collectEvents<T extends string>(
  body: ReadableStream<Uint8Array>,
  defaultEvent: T,
): Promise<Array<{ event: T; data: unknown }>> {
  const events: Array<{ event: T; data: unknown }> = []
  for await (const event of parseSSEStream<T>(body, defaultEvent)) {
    events.push(event)
  }
  return events
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('parseSSEStream', () => {
  it('단일 이벤트를 파싱한다', async () => {
    const body = createSSEStream(['event: content_delta', 'data: {"content":"hello"}', ''])
    const events = await collectEvents(body, 'content_delta')

    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('content_delta')
    expect(events[0].data).toEqual({ content: 'hello' })
  })

  it('여러 이벤트 시퀀스를 파싱한다', async () => {
    const body = createSSEStream([
      'event: message_start',
      'data: {"id":"msg-1"}',
      '',
      'event: content_delta',
      'data: {"content":"Hello "}',
      '',
      'event: content_delta',
      'data: {"content":"world!"}',
      '',
      'event: message_end',
      'data: {"done":true}',
      '',
    ])

    const events = await collectEvents(body, 'content_delta')

    expect(events).toHaveLength(4)
    expect(events.map((e) => e.event)).toEqual([
      'message_start',
      'content_delta',
      'content_delta',
      'message_end',
    ])
    expect(events[0].data).toEqual({ id: 'msg-1' })
    expect(events[1].data).toEqual({ content: 'Hello ' })
    expect(events[2].data).toEqual({ content: 'world!' })
    expect(events[3].data).toEqual({ done: true })
  })

  it('청크 분할된 데이터를 버퍼 재결합하여 파싱한다', async () => {
    const body = createChunkedStream([
      'event: content_delta\ndata: {"conte',
      'nt":"hello"}\n\nevent: message_end\ndata: {"done":true}\n\n',
    ])

    const events = await collectEvents(body, 'content_delta')

    expect(events).toHaveLength(2)
    expect(events[0].event).toBe('content_delta')
    expect(events[0].data).toEqual({ content: 'hello' })
    expect(events[1].event).toBe('message_end')
    expect(events[1].data).toEqual({ done: true })
  })

  it('잘못된 JSON 라인을 건너뛴다', async () => {
    const body = createSSEStream([
      'event: content_delta',
      'data: {invalid json}',
      '',
      'event: content_delta',
      'data: {"content":"valid"}',
      '',
    ])

    const events = await collectEvents(body, 'content_delta')

    expect(events).toHaveLength(1)
    expect(events[0].data).toEqual({ content: 'valid' })
  })

  it('event 라인이 없으면 기본 이벤트 타입을 사용한다', async () => {
    const body = createSSEStream(['data: {"content":"no event field"}', ''])

    const events = await collectEvents(body, 'content_delta')

    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('content_delta')
    expect(events[0].data).toEqual({ content: 'no event field' })
  })

  it('빈 스트림은 이벤트 없이 종료된다', async () => {
    const body = createSSEStream([])
    const events = await collectEvents(body, 'content_delta')

    expect(events).toEqual([])
  })
})

describe('streamSSEPost', () => {
  it('POST 요청으로 SSE 스트림을 수신한다', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        createSSEStream([
          'event: content_delta',
          'data: {"content":"hello"}',
          '',
          'event: message_end',
          'data: {"done":true}',
          '',
        ]),
        { status: 200 },
      ),
    )

    const events: Array<{ event: string; data: unknown }> = []
    for await (const event of streamSSEPost(
      '/api/test/stream',
      { message: 'hi' },
      undefined,
      'content_delta',
    )) {
      events.push(event)
    }

    expect(events).toHaveLength(2)
    expect(events[0].event).toBe('content_delta')
    expect(events[1].event).toBe('message_end')

    expect(globalThis.fetch).toHaveBeenCalledWith(
      `${API_BASE}/api/test/stream`,
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: 'hi' }),
      }),
    )
  })

  it('HTTP 에러 응답 시 예외를 던진다', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('Internal Server Error', { status: 500 }),
    )

    const gen = streamSSEPost('/api/test/stream', {}, undefined, 'content_delta')
    await expect(gen.next()).rejects.toThrow('Stream failed: 500')
  })

  it('response body가 null이면 예외를 던진다', async () => {
    const mockResponse = {
      ok: true,
      status: 200,
      body: null,
    } as unknown as Response

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse)

    const gen = streamSSEPost('/api/test/stream', {}, undefined, 'content_delta')
    await expect(gen.next()).rejects.toThrow('No response body')
  })
})
