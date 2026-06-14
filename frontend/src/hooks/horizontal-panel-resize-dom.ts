export function setDocumentResizeFlag(isResizing: boolean): void {
  if (typeof document === 'undefined') return

  if (isResizing) {
    document.documentElement.dataset.panelResizing = 'true'
    return
  }

  delete document.documentElement.dataset.panelResizing
}

export function requestPreviewFrame(callback: () => void): number | null {
  if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
    callback()
    return null
  }

  return window.requestAnimationFrame(callback)
}

export function cancelPreviewFrame(frameId: number | null): void {
  if (
    frameId === null ||
    typeof window === 'undefined' ||
    typeof window.cancelAnimationFrame !== 'function'
  ) {
    return
  }

  window.cancelAnimationFrame(frameId)
}
