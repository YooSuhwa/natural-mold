const EXTERNAL_WINDOW_FEATURES = 'noopener,noreferrer'
const SAFE_EXTERNAL_PROTOCOLS = new Set(['http:', 'https:', 'blob:'])

function normalizeOpenableUrl(url: string): string | null {
  if (typeof window === 'undefined') return null

  try {
    const parsed = new URL(url, window.location.origin)
    if (!SAFE_EXTERNAL_PROTOCOLS.has(parsed.protocol)) return null
    return parsed.href
  } catch {
    return null
  }
}

export function openExternalUrl(
  url: string,
  target = '_blank',
  features = EXTERNAL_WINDOW_FEATURES,
): Window | null {
  const href = normalizeOpenableUrl(url)
  if (!href) return null
  return window.open(href, target, features)
}

export function openNamedPopupWindow(name: string, features: string): Window | null {
  if (typeof window === 'undefined') return null
  return window.open('about:blank', name, features)
}

export function navigateOpenedWindow(openedWindow: Window, url: string): boolean {
  const href = normalizeOpenableUrl(url)
  if (!href) return false
  openedWindow.location.href = href
  return true
}
