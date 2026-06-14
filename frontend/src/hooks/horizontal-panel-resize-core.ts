export type HorizontalResizeSide = 'left' | 'right'

export function nextRawWidth(
  side: HorizontalResizeSide,
  startWidth: number,
  startX: number,
  clientX: number,
): number {
  const delta = clientX - startX
  return side === 'left' ? startWidth + delta : startWidth - delta
}

export function clampStableWidth(width: number, minWidth: number, maxWidth: number): number {
  if (!Number.isFinite(width)) return minWidth
  return Math.min(Math.max(width, minWidth), maxWidth)
}

export function previewWidth(width: number, minWidth: number, maxWidth: number): number {
  if (!Number.isFinite(width)) return minWidth
  if (width < minWidth) return Math.max(0, width)
  return clampStableWidth(width, minWidth, maxWidth)
}

export function shouldExpandFromCollapsedKey(key: string, side: HorizontalResizeSide): boolean {
  return (
    key === 'Home' ||
    key === 'End' ||
    (key === 'ArrowLeft' && side === 'right') ||
    (key === 'ArrowRight' && side === 'left')
  )
}
