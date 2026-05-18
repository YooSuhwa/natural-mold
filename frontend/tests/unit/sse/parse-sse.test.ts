import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createEventDeduper, parseSSEStream, streamSSEPost } from '@/lib/sse/parse-sse'

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
): Promise<Array<{ event: T; data: unknown; id?: string }>> {
  const events: Array<{ event: T; data: unknown; id?: string }> = []
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

  it('id 라인을 파싱하여 이벤트에 포함한다', async () => {
    const body = createSSEStream([
      'event: content_delta',
      'id: msg-abc-1',
      'data: {"delta":"hi"}',
      '',
      'event: content_delta',
      'id: msg-abc-2',
      'data: {"delta":" there"}',
      '',
    ])

    const events = await collectEvents(body, 'content_delta')

    expect(events).toHaveLength(2)
    expect(events[0].id).toBe('msg-abc-1')
    expect(events[1].id).toBe('msg-abc-2')
  })

  it('id 라인이 없는 이벤트는 id가 undefined이다', async () => {
    const body = createSSEStream([
      'event: content_delta',
      'data: {"delta":"hi"}',
      '',
    ])

    const events = await collectEvents(body, 'content_delta')

    expect(events[0].id).toBeUndefined()
  })

  it('이전 이벤트의 id가 다음 이벤트로 이월되지 않는다', async () => {
    const body = createSSEStream([
      'event: content_delta',
      'id: msg-1',
      'data: {"delta":"a"}',
      '',
      'event: content_delta',
      'data: {"delta":"b"}',
      '',
    ])

    const events = await collectEvents(body, 'content_delta')

    expect(events[0].id).toBe('msg-1')
    expect(events[1].id).toBeUndefined()
  })
})

describe('createEventDeduper', () => {
  it('같은 id가 두 번째로 들어오면 중복으로 판정한다', () => {
    const dedup = createEventDeduper()

    expect(dedup.isDuplicate('msg-1')).toBe(false)
    expect(dedup.isDuplicate('msg-1')).toBe(true)
  })

  it('서로 다른 id는 모두 통과한다', () => {
    const dedup = createEventDeduper()

    expect(dedup.isDuplicate('msg-1')).toBe(false)
    expect(dedup.isDuplicate('msg-2')).toBe(false)
    expect(dedup.isDuplicate('msg-3')).toBe(false)
    expect(dedup.size()).toBe(3)
  })

  it('id가 undefined이면 항상 통과한다 (구버전 백엔드 호환)', () => {
    const dedup = createEventDeduper()

    expect(dedup.isDuplicate(undefined)).toBe(false)
    expect(dedup.isDuplicate(undefined)).toBe(false)
    expect(dedup.size()).toBe(0)
  })

  it('reset()은 누적된 id를 비운다', () => {
    const dedup = createEventDeduper()

    dedup.isDuplicate('msg-1')
    dedup.isDuplicate('msg-2')
    dedup.reset()

    expect(dedup.size()).toBe(0)
    // reset 후 같은 id 다시 통과
    expect(dedup.isDuplicate('msg-1')).toBe(false)
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

    // fetchEventSource는 SSE 표준에 따라 ``Accept: text/event-stream``을 추가
    // 발행하므로 헤더는 partial 매칭으로 검증한다.
    expect(globalThis.fetch).toHaveBeenCalledWith(
      `${API_BASE}/api/test/stream`,
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({ message: 'hi' }),
      }),
    )
  })

  it('HTTP 에러 응답 시 예외를 던진다', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('Internal Server Error', { status: 500 }),
    )

    const gen = streamSSEPost('/api/test/stream', {}, undefined, 'content_delta')
    // i18n contract: stream errors surface the user-facing Korean fallback
    // when no structured ``{error: {code, message}}`` body is present.
    await expect(gen.next()).rejects.toThrow('요청이 거부되었습니다 (HTTP 500)')
  })

  // body가 null인 응답 처리는 fetchEventSource 라이브러리 내부 로직이 담당하게
  // 됐으므로 caller side 단위 테스트는 더 이상 의미 없음 → 삭제.
})
