import { describe, expect, it, vi } from 'vitest'
import { withAutoResume } from '../with-auto-resume'
import { StreamHttpError } from '../parse-sse'

interface TestEvent {
  id?: string
  payload: number
}

async function* fromArray(events: TestEvent[]): AsyncGenerator<TestEvent> {
  for (const ev of events) yield ev
}

async function* throwAfter(
  events: TestEvent[],
  err: unknown,
): AsyncGenerator<TestEvent> {
  for (const ev of events) yield ev
  throw err
}

async function collect<T>(gen: AsyncGenerator<T>): Promise<T[]> {
  const out: T[] = []
  for await (const v of gen) out.push(v)
  return out
}

describe('withAutoResume', () => {
  it('primary 가 정상 종료하면 resume 을 호출하지 않는다', async () => {
    const resume = vi.fn(() => null)
    const result = await collect(
      withAutoResume(
        () => fromArray([{ id: 'a-1', payload: 1 }, { id: 'a-2', payload: 2 }]),
        resume,
      ),
    )
    expect(result.map((e) => e.payload)).toEqual([1, 2])
    expect(resume).not.toHaveBeenCalled()
  })

  it('primary 가 throw 하면 lastEventId 와 함께 resume 을 호출하고 이어 받는다', async () => {
    const resume = vi.fn((lastEventId, attempt) => {
      expect(lastEventId).toBe('a-2')
      expect(attempt).toBe(1)
      return fromArray([{ id: 'a-3', payload: 3 }, { id: 'a-4', payload: 4 }])
    })
    const result = await collect(
      withAutoResume<TestEvent>(
        () =>
          throwAfter(
            [{ id: 'a-1', payload: 1 }, { id: 'a-2', payload: 2 }],
            new TypeError('network'),
          ),
        resume,
        { backoffMs: [0] },
      ),
    )
    expect(result.map((e) => e.payload)).toEqual([1, 2, 3, 4])
    expect(resume).toHaveBeenCalledOnce()
  })

  it('콜백 호출 순서: onReconnecting → 첫 event → onReconnected', async () => {
    const calls: string[] = []
    await collect(
      withAutoResume<TestEvent>(
        () =>
          throwAfter([{ id: 'a-1', payload: 1 }], new TypeError('boom')),
        () => fromArray([{ id: 'a-2', payload: 2 }]),
        {
          backoffMs: [0],
          onReconnecting: (attempt) => calls.push(`reconnecting:${attempt}`),
          onReconnected: () => calls.push('reconnected'),
          onFailed: () => calls.push('failed'),
        },
      ),
    )
    expect(calls).toEqual(['reconnecting:1', 'reconnected'])
  })

  it('maxAttempts 초과 시 onFailed 후 throw', async () => {
    const onFailed = vi.fn()
    const onReconnecting = vi.fn()
    await expect(
      collect(
        withAutoResume<TestEvent>(
          () => throwAfter([], new TypeError('boom')),
          () => {
            return (async function* () {
              throw new TypeError('still broken')
            })()
          },
          { backoffMs: [0], maxAttempts: 2, onReconnecting, onFailed },
        ),
      ),
    ).rejects.toThrow()
    expect(onReconnecting).toHaveBeenCalledTimes(2)
    expect(onFailed).toHaveBeenCalledOnce()
  })

  it('4xx StreamHttpError 는 retry 하지 않는다 (RESUME_NOT_FOUND/INTERRUPT_PENDING)', async () => {
    const resume = vi.fn(() => fromArray([{ id: 'a-2', payload: 2 }]))
    const onFailed = vi.fn()
    await expect(
      collect(
        withAutoResume<TestEvent>(
          () => throwAfter([], new StreamHttpError(404, 'Not Found')),
          resume,
          { backoffMs: [0], onFailed },
        ),
      ),
    ).rejects.toBeInstanceOf(StreamHttpError)
    expect(resume).not.toHaveBeenCalled()
    expect(onFailed).toHaveBeenCalledOnce()
  })

  it('AbortError 는 retry 없이 그대로 전파', async () => {
    const resume = vi.fn(() => fromArray([{ id: 'a-2', payload: 2 }]))
    await expect(
      collect(
        withAutoResume<TestEvent>(
          () =>
            throwAfter([], new DOMException('Aborted', 'AbortError')),
          resume,
          { backoffMs: [0] },
        ),
      ),
    ).rejects.toBeInstanceOf(DOMException)
    expect(resume).not.toHaveBeenCalled()
  })

  it('signal.aborted 면 retry 하지 않는다', async () => {
    const controller = new AbortController()
    controller.abort()
    const resume = vi.fn(() => fromArray([{ id: 'a-2', payload: 2 }]))
    await expect(
      collect(
        withAutoResume<TestEvent>(
          () => throwAfter([], new TypeError('boom')),
          resume,
          { backoffMs: [0], signal: controller.signal },
        ),
      ),
    ).rejects.toThrow()
    expect(resume).not.toHaveBeenCalled()
  })

  it('resumeFactory 가 null 을 반환하면 더 이상 retry 하지 않는다', async () => {
    const onFailed = vi.fn()
    await expect(
      collect(
        withAutoResume<TestEvent>(
          () =>
            throwAfter([{ id: 'a-1', payload: 1 }], new TypeError('boom')),
          () => null,
          { backoffMs: [0], onFailed },
        ),
      ),
    ).rejects.toThrow()
    expect(onFailed).toHaveBeenCalledOnce()
  })

  it('재시도 stream 이 정상 종료 후 또 throw 하면 attempt 가 reset 되어 max 까지 다시 시도', async () => {
    const sources: AsyncGenerator<TestEvent>[] = [
      throwAfter([{ id: 'a-1', payload: 1 }], new TypeError('1')),
      throwAfter([{ id: 'a-2', payload: 2 }], new TypeError('2')),
      throwAfter([{ id: 'a-3', payload: 3 }], new TypeError('3')),
      fromArray([{ id: 'a-4', payload: 4 }]),
    ]
    let primaryConsumed = false
    const primary = () => {
      if (primaryConsumed) throw new Error('primary called twice')
      primaryConsumed = true
      return sources[0]
    }
    let resumeIdx = 1
    const result = await collect(
      withAutoResume<TestEvent>(
        primary,
        () => {
          const next = sources[resumeIdx]
          resumeIdx += 1
          return next
        },
        { backoffMs: [0], maxAttempts: 5 },
      ),
    )
    expect(result.map((e) => e.payload)).toEqual([1, 2, 3, 4])
  })

  it('backoff sleep 도중 abort 되면 retry 없이 즉시 throw', async () => {
    const controller = new AbortController()
    const onFailed = vi.fn()
    const resume = vi.fn(() => fromArray([{ id: 'a-2', payload: 2 }]))
    const promise = collect(
      withAutoResume<TestEvent>(
        () => throwAfter([], new TypeError('boom')),
        resume,
        { backoffMs: [10000], signal: controller.signal, onFailed },
      ),
    )
    // backoff 시작 시점을 보장 — 한 microtask 양보 후 abort.
    await Promise.resolve()
    controller.abort()
    await expect(promise).rejects.toBeInstanceOf(DOMException)
    expect(resume).not.toHaveBeenCalled()
    expect(onFailed).toHaveBeenCalledOnce()
  })

  it('resumeFactory 가 sync throw 하면 caller 로 그대로 전파', async () => {
    const onFailed = vi.fn()
    const fatal = new Error('factory fatal')
    await expect(
      collect(
        withAutoResume<TestEvent>(
          () => throwAfter([], new TypeError('boom')),
          () => {
            throw fatal
          },
          { backoffMs: [0], onFailed },
        ),
      ),
    ).rejects.toBe(fatal)
  })

  it('primary 가 0개 event 후 throw 해도 resume 은 lastEventId=undefined 로 호출', async () => {
    const resume = vi.fn((lastEventId) => {
      expect(lastEventId).toBeUndefined()
      return fromArray([{ id: 'a-1', payload: 1 }])
    })
    const result = await collect(
      withAutoResume<TestEvent>(
        () => throwAfter([], new TypeError('boom')),
        resume,
        { backoffMs: [0] },
      ),
    )
    expect(result.map((e) => e.payload)).toEqual([1])
    expect(resume).toHaveBeenCalledOnce()
  })

  it('id 가 없는 event 는 lastEventId 를 갱신하지 않는다', async () => {
    const resume = vi.fn((lastEventId) => {
      expect(lastEventId).toBe('a-1')
      return fromArray([{ id: 'a-2', payload: 2 }])
    })
    await collect(
      withAutoResume<TestEvent>(
        () =>
          throwAfter(
            [{ id: 'a-1', payload: 1 }, { payload: 99 }],
            new TypeError('boom'),
          ),
        resume,
        { backoffMs: [0] },
      ),
    )
    expect(resume).toHaveBeenCalledOnce()
  })
})
