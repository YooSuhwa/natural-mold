import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'

export const DRAG_START_THRESHOLD_PX = 4

export interface PanelWidthBounds {
  min: number
  max: number
}

export interface StoredPanelWidthConfig {
  defaultWidth: number
  minWidth: number
  maxWidth: number
}

type PanelWidthUpdate = number | ((currentWidth: number) => number)

function toFiniteWidth(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return fallback
}

export function clampPanelWidth(width: number, bounds: PanelWidthBounds): number {
  if (!Number.isFinite(width)) return bounds.min
  return Math.min(Math.max(width, bounds.min), bounds.max)
}

export function createStoredPanelWidthAtom(key: string, config: StoredPanelWidthConfig) {
  const bounds = { min: config.minWidth, max: config.maxWidth }
  const defaultWidth = clampPanelWidth(config.defaultWidth, bounds)
  const storedWidthAtom = atomWithStorage<unknown>(key, defaultWidth, undefined, {
    getOnInit: true,
  })

  return atom(
    (get) => clampPanelWidth(toFiniteWidth(get(storedWidthAtom), defaultWidth), bounds),
    (get, set, nextWidth: PanelWidthUpdate) => {
      const currentWidth = clampPanelWidth(
        toFiniteWidth(get(storedWidthAtom), defaultWidth),
        bounds,
      )
      const nextValue = typeof nextWidth === 'function' ? nextWidth(currentWidth) : nextWidth

      set(storedWidthAtom, clampPanelWidth(toFiniteWidth(nextValue, defaultWidth), bounds))
    },
  )
}
