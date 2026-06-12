/**
 * streamSSEPost abort 회귀 테스트.
 *
 * `@microsoft/fetch-event-source` 는 input signal 이 abort 되면 promise 를
 * reject 가 아니라 **resolve** 하고 onclose/onerror 도 호출하지 않는다.
 * bridge 가 catch 에서만 closed 를 세우면 소비 루프가 영원히 대기하는
 * deadlock 이 된다 — Stop(서버 cancel 후 local abort) 시 isRunning 이
 * 풀리지 않던 durable run cancel 회귀의 근본 원인.
 */
import { describe, expect, it, vi } from 'vitest'

type FesInit = { signal?: AbortSignal }

const fesMock = vi.hoisted(() => ({
  impl: null as null | ((url: string, init: FesInit) => Promise<void>),
}))

vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: (url: string, init: FesInit) => {
    if (!fesMock.impl) throw new Error('fetchEventSource mock not configured')
    return fesMock.impl(url, init)
  },
}))

import { streamSSEPost } from '../parse-sse'

describe('streamSSEPost abort handling', () => {
  it('abort 시 fetch-event-source 가 resolve 만 해도 AbortError 로 종료된다 (deadlock 방지)', async () => {
    // 라이브러리의 실제 abort 동작 재현: reject 없이 resolve, onclose/onerror 미호출
    fesMock.impl = (_url, init) =>
      new Promise<void>((resolve) => {
        const signal = init.signal
        if (!signal) return
        if (signal.aborted) {
          resolve()
          return
        }
        signal.addEventListener('abort', () => resolve(), { once: true })
      })

    const controller = new AbortController()
    const stream = streamSSEPost(
      '/api/conversations/x/messages',
      {},
      controller.signal,
      'content_delta',
    )

    const pending = stream.next()
    // 소비 루프가 resolver 대기에 진입할 시간을 준 뒤 abort
    await new Promise((resolve) => setTimeout(resolve, 10))
    controller.abort()

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  })
})
