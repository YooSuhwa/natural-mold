const FIX_INITIAL_MESSAGE_KEY = 'fix-initial-message'

export function storeFixInitialMessage(message: string): void {
  if (typeof window === 'undefined') return

  try {
    window.sessionStorage.setItem(FIX_INITIAL_MESSAGE_KEY, message)
  } catch {
    return
  }
}

export function consumeFixInitialMessage(): string | null {
  if (typeof window === 'undefined') return null

  try {
    const message = window.sessionStorage.getItem(FIX_INITIAL_MESSAGE_KEY)
    if (message) window.sessionStorage.removeItem(FIX_INITIAL_MESSAGE_KEY)
    return message
  } catch {
    return null
  }
}
