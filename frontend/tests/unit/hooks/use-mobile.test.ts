import * as React from 'react'
import { act, renderHook } from '@testing-library/react'
import { hydrateRoot } from 'react-dom/client'
import { renderToString } from 'react-dom/server'
import { useIsMobile } from '@/hooks/use-mobile'

function HydrationBranchProbe() {
  const isMobile = useIsMobile()

  return isMobile
    ? React.createElement('main', { 'data-slot': 'sidebar-inset' })
    : React.createElement('div', { 'data-slot': 'sidebar' })
}

describe('useIsMobile', () => {
  const originalInnerWidth = window.innerWidth
  const originalMatchMedia = window.matchMedia
  const listeners = new Set<() => void>()
  const removeEventListener = vi.fn()

  beforeEach(() => {
    listeners.clear()
    removeEventListener.mockClear()
    // jsdom may not have matchMedia, so define it directly
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: window.innerWidth < 768,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: (_: string, cb: () => void) => {
        listeners.add(cb)
      },
      removeEventListener: (_: string, cb: () => void) => {
        listeners.delete(cb)
        removeEventListener()
      },
      dispatchEvent: vi.fn(),
    }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    Object.defineProperty(window, 'innerWidth', {
      value: originalInnerWidth,
      writable: true,
    })
    window.matchMedia = originalMatchMedia
  })

  it('returns false at the 768px desktop breakpoint', () => {
    Object.defineProperty(window, 'innerWidth', {
      value: 768,
      writable: true,
    })
    const { result } = renderHook(() => useIsMobile())
    expect(result.current).toBe(false)
  })

  it('returns true at the 767px mobile breakpoint', () => {
    Object.defineProperty(window, 'innerWidth', {
      value: 767,
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

  it('does not report a recoverable hydration error at a 390px viewport', async () => {
    Object.defineProperty(window, 'innerWidth', {
      value: 390,
      writable: true,
    })
    const serverWindow = window
    const container = document.createElement('div')
    const recoverableErrors: unknown[] = []

    vi.stubGlobal('window', undefined)
    let markup: string
    try {
      markup = renderToString(React.createElement(HydrationBranchProbe))
    } finally {
      vi.stubGlobal('window', serverWindow)
    }

    container.innerHTML = markup
    document.body.append(container)
    const root = hydrateRoot(container, React.createElement(HydrationBranchProbe), {
      onRecoverableError: (error) => {
        recoverableErrors.push(error)
      },
    })

    try {
      await act(async () => {
        await Promise.resolve()
      })

      expect(recoverableErrors).toHaveLength(0)
      expect(container.querySelector('[data-slot="sidebar-inset"]')).not.toBeNull()
    } finally {
      await act(async () => {
        root.unmount()
      })
      container.remove()
    }
  })

  it('removes the media-query listener when unmounted', () => {
    const { unmount } = renderHook(() => useIsMobile())

    unmount()

    expect(removeEventListener).toHaveBeenCalledTimes(1)
    expect(listeners.size).toBe(0)
  })
})
