/**
 * A per-browser boolean convenience flag in localStorage (a dismissed notice and the
 * like). Storage can be missing or throw (private windows, blocked site data, previews):
 * reads then report `false` and writes are dropped, so callers must work without it.
 */
export function readLocalFlag(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

export function writeLocalFlag(key: string): void {
  try {
    window.localStorage.setItem(key, '1')
  } catch {
    // Storage unavailable: the flag lasts for this visit only.
  }
}
