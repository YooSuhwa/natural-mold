import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useApprovalDeadline } from '../use-approval-deadline'

describe('useApprovalDeadline', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-05T00:00:00Z'))
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it('initializes the remaining value on the immediate tick', async () => {
    const onExpire = vi.fn()
    const { result, unmount } = renderHook(() =>
      useApprovalDeadline({ approvalId: 'approval-1', initialTimeoutSeconds: 120, onExpire }),
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    try {
      expect(result.current.remaining).toBe(120)
      expect(result.current.formatted).toBe('2:00')
    } finally {
      unmount()
    }
  })

  it('decrements remaining after one full-second tick', async () => {
    const onExpire = vi.fn()
    const { result, unmount } = renderHook(() =>
      useApprovalDeadline({ approvalId: 'approval-1', initialTimeoutSeconds: 120, onExpire }),
    )

    try {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0)
        await vi.advanceTimersByTimeAsync(1000)
      })

      expect(result.current.remaining).toBe(119)
      expect(result.current.formatted).toBe('1:59')
      expect(onExpire).not.toHaveBeenCalled()
    } finally {
      unmount()
    }
  })
})
