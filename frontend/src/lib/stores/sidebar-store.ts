import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'
import { clampPanelWidth, createStoredPanelWidthAtom, DRAG_START_THRESHOLD_PX } from './panel-width'

export const SIDEBAR_WIDTH_DEFAULT_PX = 256
export const SIDEBAR_WIDTH_MIN_PX = 224
export const SIDEBAR_WIDTH_MAX_PX = 420
export const SIDEBAR_COLLAPSE_THRESHOLD_PX = 180
export const SIDEBAR_OPEN_COOKIE_NAME = 'sidebar_state'
export const SIDEBAR_OPEN_COOKIE_MAX_AGE = 60 * 60 * 24 * 7
export const SIDEBAR_WIDTH_COOKIE_NAME = 'moldy_sidebar_width'
export const SIDEBAR_WIDTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 400
export { DRAG_START_THRESHOLD_PX }

export const sidebarOpenAtom = atom(true)

export function clampSidebarWidth(width: number): number {
  return clampPanelWidth(width, {
    min: SIDEBAR_WIDTH_MIN_PX,
    max: SIDEBAR_WIDTH_MAX_PX,
  })
}

export function readSidebarWidthCookie(value: string | undefined): number | null {
  if (!value) return null

  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return null

  return clampSidebarWidth(parsed)
}

export function writeSidebarOpenCookie(open: boolean): void {
  document.cookie = `${SIDEBAR_OPEN_COOKIE_NAME}=${open}; path=/; max-age=${SIDEBAR_OPEN_COOKIE_MAX_AGE}`
}

export function writeSidebarWidthCookie(width: number): void {
  const nextWidth = clampSidebarWidth(width)
  document.cookie = `${SIDEBAR_WIDTH_COOKIE_NAME}=${nextWidth}; path=/; max-age=${SIDEBAR_WIDTH_COOKIE_MAX_AGE}`
}

export const sidebarWidthAtom = createStoredPanelWidthAtom('moldy.sidebar.widthPx', {
  defaultWidth: SIDEBAR_WIDTH_DEFAULT_PX,
  minWidth: SIDEBAR_WIDTH_MIN_PX,
  maxWidth: SIDEBAR_WIDTH_MAX_PX,
})

export const featuresExpandedAtom = atomWithStorage('moldy.sidebar.featuresExpanded', true)
