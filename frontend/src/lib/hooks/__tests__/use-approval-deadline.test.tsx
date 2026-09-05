import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useApprovalDeadline } from '../use-approval-deadline'

describe('useApprovalDeadline', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-05T00:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps the same remaining value on a subsecond tick and advances on a full tick', () => {
    const onExpire = vi.fn()
    const { result } = renderHook(() =>
      useApprovalDeadline({ approvalId: 'approval-1', initialTimeoutSeconds: 120, onExpire }),
    )

    act(() => vi.advanceTimersByTime(0))
    expect(result.current.remaining).toBe(120)

    act(() => vi.advanceTimersByTime(1000))
    expect(result.current.remaining).toBe(119)
    expect(onExpire).not.toHaveBeenCalled()
  })
})
