/**
 * HiTL resume wire body shape 회귀 가드 — `streamResumeDecisions`가
 * `{decisions: [...]}` body를 보내는지 검증.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Decision } from '@/lib/types'
import { streamResumeDecisions } from '../stream-resume'

// ---------------------------------------------------------------------------
// fetchEventSource를 가짜로 교체 — body 만 캡처하면 충분 (스트림 자체는 빈
// generator로 즉시 close). 실제 네트워크/SSE 파싱은 검증 대상이 아님.
// ---------------------------------------------------------------------------

interface CapturedCall {
  url: string
  method: string
  body: unknown
  headers: Record<string, string>
}

const captured: CapturedCall[] = []

vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: vi.fn(
    async (
      url: string,
      init: {
        method?: string
        body?: string
        headers?: Record<string, string>
        onopen?: (response: Response) => Promise<void> | void
        onmessage?: (msg: { event: string; data: string; id?: string }) => void
        onclose?: () => void
        onerror?: (err: unknown) => void
      },
    ) => {
      captured.push({
        url,
        method: init.method ?? 'GET',
        body: init.body ? JSON.parse(init.body as string) : undefined,
        headers: init.headers ?? {},
      })
      // 200 응답 시뮬레이션 → onopen 통과 → 즉시 onclose.
      const fakeResponse = {
        ok: true,
        status: 200,
        headers: { get: () => null },
      } as unknown as Response
      await init.onopen?.(fakeResponse)
      init.onclose?.()
    },
  ),
}))

async function drain<T>(gen: AsyncGenerator<T>): Promise<T[]> {
  const out: T[] = []
  for await (const v of gen) out.push(v)
  return out
}

beforeEach(() => {
  captured.length = 0
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('streamResumeDecisions', () => {
  it('body는 정확히 {decisions: [...]} 형태로 직렬화된다', async () => {
    const decisions: Decision[] = [
      { type: 'approve' },
      {
        type: 'edit',
        edited_action: { name: 'send_email', args: { to: 'x@y' } },
      },
      { type: 'reject', message: 'not allowed' },
      { type: 'respond', message: 'user reply' },
    ]

    await drain(streamResumeDecisions('conv-1', decisions))

    expect(captured).toHaveLength(1)
    expect(captured[0].method).toBe('POST')
    expect(captured[0].url).toContain('/api/conversations/conv-1/messages/resume')
    expect(captured[0].body).toEqual({ decisions })
    // 표준 wire는 절대로 legacy `response` 필드를 같이 보내지 않는다.
    expect(captured[0].body).not.toHaveProperty('response')
  })

  it('빈 decisions 배열도 그대로 송신 (validation은 미들웨어에 위임)', async () => {
    await drain(streamResumeDecisions('conv-2', []))
    expect(captured[0].body).toEqual({ decisions: [] })
  })

  it('Content-Type: application/json 헤더가 설정된다', async () => {
    await drain(streamResumeDecisions('conv-3', [{ type: 'approve' }]))
    expect(captured[0].headers['Content-Type']).toBe('application/json')
    expect(captured[0].headers['Accept']).toBe('text/event-stream')
  })
})
