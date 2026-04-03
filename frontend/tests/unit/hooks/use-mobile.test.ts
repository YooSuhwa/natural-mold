import { renderHook, act } from '@testing-library/react'
import { useIsMobile } from '@/hooks/use-mobile'

describe('useIsMobile', () => {
  const originalInnerWidth = window.innerWidth
  const listeners: Array<() => void> = []

  beforeEach(() => {
    listeners.length = 0
    // jsdom may not have matchMedia, so define it directly
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: (_: string, cb: () => void) => {
        listeners.push(cb)
      },
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  })

  afterEach(() => {
    Object.defineProperty(window, 'innerWidth', {
      value: originalInnerWidth,
      writable: true,
    })
  })

  it('returns false on desktop width', () => {
    Object.defineProperty(window, 'innerWidth', {
      value: 1024,
      writable: true,
    })
    const { result } = renderHook(() => useIsMobile())
    expect(result.current).toBe(false)
  })

  it('returns true on mobile width', () => {
    Object.defineProperty(window, 'innerWidth', {
      value: 400,
      writable: true,
    })
    const { result } = renderHook(() => useIsMobile())
    expect(result.current).toBe(true)
  })

  it('updates when window resizes', () => {
    Object.defineProperty(window, 'innerWidth', {
      value: 1024,
      writable: true,
    })
    const { result } = renderHook(() => useIsMobile())
    expect(result.current).toBe(false)

    act(() => {
      Object.defineProperty(window, 'innerWidth', {
        value: 400,
        writable: true,
      })
      listeners.forEach((cb) => cb())
    })

    expect(result.current).toBe(true)
  })
})
