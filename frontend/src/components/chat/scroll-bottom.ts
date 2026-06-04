export interface ThreadViewportScrollMetrics {
  scrollHeight: number
  scrollTop: number
  clientHeight: number
}

export function isThreadViewportAtBottom({
  scrollHeight,
  scrollTop,
  clientHeight,
}: ThreadViewportScrollMetrics): boolean {
  return Math.abs(scrollHeight - scrollTop - clientHeight) <= 1 || scrollHeight <= clientHeight
}
