import { createStore } from 'jotai'
import { beforeEach, describe, expect, it } from 'vitest'
import { clampPanelWidth, createStoredPanelWidthAtom } from '@/lib/stores/panel-width'

describe('panel width helpers', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('clamps widths to the provided bounds', () => {
    expect(clampPanelWidth(120, { min: 160, max: 420 })).toBe(160)
    expect(clampPanelWidth(500, { min: 160, max: 420 })).toBe(420)
    expect(clampPanelWidth(320, { min: 160, max: 420 })).toBe(320)
    expect(clampPanelWidth(Number.NaN, { min: 160, max: 420 })).toBe(160)
  })

  it('creates a persisted width atom with a clamped default', () => {
    const widthAtom = createStoredPanelWidthAtom('test.panelWidth.default', {
      defaultWidth: 120,
      minWidth: 160,
      maxWidth: 420,
    })
    const store = createStore()

    expect(store.get(widthAtom)).toBe(160)
  })

  it('clamps writes before storing them', () => {
    const widthAtom = createStoredPanelWidthAtom('test.panelWidth.write', {
      defaultWidth: 256,
      minWidth: 160,
      maxWidth: 420,
    })
    const store = createStore()

    store.set(widthAtom, 120)
    expect(store.get(widthAtom)).toBe(160)
    expect(window.localStorage.getItem('test.panelWidth.write')).toBe('160')

    store.set(widthAtom, 500)
    expect(store.get(widthAtom)).toBe(420)
    expect(window.localStorage.getItem('test.panelWidth.write')).toBe('420')
  })

  it('clamps stored localStorage values on read', () => {
    window.localStorage.setItem('test.panelWidth.read', '900')
    const widthAtom = createStoredPanelWidthAtom('test.panelWidth.read', {
      defaultWidth: 256,
      minWidth: 160,
      maxWidth: 420,
    })
    const store = createStore()

    expect(store.get(widthAtom)).toBe(420)
  })
})
