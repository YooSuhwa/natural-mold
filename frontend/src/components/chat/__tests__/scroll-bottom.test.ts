import { describe, expect, it } from 'vitest'

import { isThreadViewportAtBottom } from '../scroll-bottom'

describe('isThreadViewportAtBottom', () => {
  it('returns true when the viewport is scrolled to the bottom', () => {
    expect(
      isThreadViewportAtBottom({
        scrollHeight: 2750,
        scrollTop: 2054,
        clientHeight: 696,
      }),
    ).toBe(true)
  })

  it('treats one-pixel browser rounding differences as bottom', () => {
    expect(
      isThreadViewportAtBottom({
        scrollHeight: 2782,
        scrollTop: 2087,
        clientHeight: 696,
      }),
    ).toBe(true)
  })

  it('returns false when the viewport is above the bottom', () => {
    expect(
      isThreadViewportAtBottom({
        scrollHeight: 2750,
        scrollTop: 655,
        clientHeight: 696,
      }),
    ).toBe(false)
  })

  it('returns true when content does not overflow', () => {
    expect(
      isThreadViewportAtBottom({
        scrollHeight: 452,
        scrollTop: 0,
        clientHeight: 452,
      }),
    ).toBe(true)
  })
})
