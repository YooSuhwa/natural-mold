import { createStore } from 'jotai'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  clampSidebarWidth,
  readSidebarWidthCookie,
  sidebarOpenAtom,
  sidebarWidthAtom,
  SIDEBAR_COLLAPSE_THRESHOLD_PX,
  SIDEBAR_OPEN_COOKIE_MAX_AGE,
  SIDEBAR_OPEN_COOKIE_NAME,
  SIDEBAR_WIDTH_COOKIE_MAX_AGE,
  SIDEBAR_WIDTH_COOKIE_NAME,
  SIDEBAR_WIDTH_DEFAULT_PX,
  SIDEBAR_WIDTH_MAX_PX,
  SIDEBAR_WIDTH_MIN_PX,
  writeSidebarOpenCookie,
  writeSidebarWidthCookie,
} from '@/lib/stores/sidebar-store'

describe('sidebar-store atoms', () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.cookie = `${SIDEBAR_OPEN_COOKIE_NAME}=; path=/; max-age=0`
    document.cookie = `${SIDEBAR_WIDTH_COOKIE_NAME}=; path=/; max-age=0`
  })

  it('sidebarOpenAtom defaults to true', () => {
    const store = createStore()
    expect(store.get(sidebarOpenAtom)).toBe(true)
  })

  it('sidebarOpenAtom can be toggled', () => {
    const store = createStore()
    store.set(sidebarOpenAtom, false)
    expect(store.get(sidebarOpenAtom)).toBe(false)
    store.set(sidebarOpenAtom, true)
    expect(store.get(sidebarOpenAtom)).toBe(true)
  })

  it('exposes the sidebar width contract', () => {
    expect(SIDEBAR_OPEN_COOKIE_NAME).toBe('sidebar_state')
    expect(SIDEBAR_OPEN_COOKIE_MAX_AGE).toBe(60 * 60 * 24 * 7)
    expect(SIDEBAR_WIDTH_COOKIE_NAME).toBe('moldy_sidebar_width')
    expect(SIDEBAR_WIDTH_COOKIE_MAX_AGE).toBe(60 * 60 * 24 * 400)
    expect(SIDEBAR_WIDTH_DEFAULT_PX).toBe(256)
    expect(SIDEBAR_WIDTH_MIN_PX).toBe(224)
    expect(SIDEBAR_WIDTH_MAX_PX).toBe(420)
    expect(SIDEBAR_COLLAPSE_THRESHOLD_PX).toBe(180)
  })

  it('clamps sidebar widths to the expanded range', () => {
    expect(clampSidebarWidth(160)).toBe(224)
    expect(clampSidebarWidth(320)).toBe(320)
    expect(clampSidebarWidth(500)).toBe(420)
  })

  it('parses and clamps the sidebar width cookie', () => {
    expect(readSidebarWidthCookie(undefined)).toBeNull()
    expect(readSidebarWidthCookie('not-a-number')).toBeNull()
    expect(readSidebarWidthCookie('180')).toBe(224)
    expect(readSidebarWidthCookie('300')).toBe(300)
    expect(readSidebarWidthCookie('900')).toBe(420)
  })

  it('writes sidebar preference cookies', () => {
    writeSidebarOpenCookie(false)
    writeSidebarWidthCookie(900)

    expect(document.cookie).toContain(`${SIDEBAR_OPEN_COOKIE_NAME}=false`)
    expect(document.cookie).toContain(`${SIDEBAR_WIDTH_COOKIE_NAME}=420`)
  })

  it('persists a clamped sidebar width preference', () => {
    const store = createStore()

    expect(store.get(sidebarWidthAtom)).toBe(256)
    store.set(sidebarWidthAtom, 120)

    expect(store.get(sidebarWidthAtom)).toBe(224)
    expect(window.localStorage.getItem('moldy.sidebar.widthPx')).toBe('224')
  })
})
