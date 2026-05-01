import { describe, it, expect } from 'vitest'
import { createStreamGuard } from '@/lib/sse/stream-guard'

describe('createStreamGuard', () => {
  it('begin()이 단조 증가하는 token을 발급한다', () => {
    const guard = createStreamGuard()
    const t1 = guard.begin()
    const t2 = guard.begin()
    const t3 = guard.begin()
    expect(t1).toBe(1)
    expect(t2).toBe(2)
    expect(t3).toBe(3)
  })

  it('가장 최근에 발급된 token만 isStale=false', () => {
    const guard = createStreamGuard()
    const t1 = guard.begin()
    expect(guard.isStale(t1)).toBe(false)

    const t2 = guard.begin()
    // t1은 더 이상 활성 stream이 아님
    expect(guard.isStale(t1)).toBe(true)
    expect(guard.isStale(t2)).toBe(false)
  })

  it('begin() 호출 시 dedup 카운터가 reset된다', () => {
    const guard = createStreamGuard()
    guard.begin()
    expect(guard.isDuplicate('msg-1')).toBe(false)
    expect(guard.isDuplicate('msg-1')).toBe(true)

    // 새 stream 시작 → 같은 id 다시 통과
    guard.begin()
    expect(guard.isDuplicate('msg-1')).toBe(false)
  })

  it('id가 undefined면 항상 통과한다 (구버전 백엔드 호환)', () => {
    const guard = createStreamGuard()
    guard.begin()
    expect(guard.isDuplicate(undefined)).toBe(false)
    expect(guard.isDuplicate(undefined)).toBe(false)
  })

  it('서로 다른 id는 모두 통과', () => {
    const guard = createStreamGuard()
    guard.begin()
    expect(guard.isDuplicate('msg-1')).toBe(false)
    expect(guard.isDuplicate('msg-2')).toBe(false)
    expect(guard.isDuplicate('msg-3')).toBe(false)
  })

  it('typical race 시나리오 — 이전 stream의 stale event를 token 비교로 폐기', () => {
    const guard = createStreamGuard()

    // Stream A 시작
    const tokenA = guard.begin()
    expect(guard.isStale(tokenA)).toBe(false)

    // 사용자가 Stream B로 전환 (Edit/Regenerate/cancel)
    const tokenB = guard.begin()

    // Stream A의 generator가 비동기로 늦게 yield한 chunk
    // → consumer가 isStale(tokenA)로 검증해 폐기 가능
    expect(guard.isStale(tokenA)).toBe(true)
    expect(guard.isStale(tokenB)).toBe(false)
  })
})
