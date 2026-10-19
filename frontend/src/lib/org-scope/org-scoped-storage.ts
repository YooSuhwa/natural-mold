/**
 * Web-storage keys whose values hold org-scoped resource ids or content
 * (plan 13.11). They are cleared on organization switch; pure display
 * preferences (widths, sort order, toggles) stay global.
 */
export const ORG_SCOPED_LOCAL_STORAGE_KEYS = [
  'moldy.chatNavigator.expandedAgentIds',
  'moldy.chatNavigator.collapsedAgentIds',
  'moldy.chatNavigator.expandedListScopes',
] as const

export const ORG_SCOPED_SESSION_STORAGE_KEYS = ['fix-initial-message'] as const

export function clearOrgScopedStorage(): void {
  if (typeof window === 'undefined') return
  removeKeys(() => window.localStorage, ORG_SCOPED_LOCAL_STORAGE_KEYS)
  removeKeys(() => window.sessionStorage, ORG_SCOPED_SESSION_STORAGE_KEYS)
}

function removeKeys(getStorage: () => Storage, keys: readonly string[]): void {
  try {
    const storage = getStorage()
    for (const key of keys) storage.removeItem(key)
  } catch {
    // Storage can be unavailable (private mode, blocked site data).
  }
}
