/**
 * sessionStorage flag keys for auth-related one-shot UI events.
 *
 * Centralized so register/login mutation, OnboardingDialog, and the
 * super-user welcome toast hook agree on the exact keys. Renaming a
 * flag here resets the dismissed state for every user — handle with care.
 */

export const ONBOARDING_DISMISSED_FLAG = 'moldy.onboarding_dismissed'
export const SUPER_USER_WELCOMED_FLAG = 'moldy.super_user_welcomed'

function readSessionFlag(key: string): string | null {
  if (typeof window === 'undefined') return null

  try {
    return window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function writeSessionFlag(key: string, value: string): boolean {
  if (typeof window === 'undefined') return false

  try {
    window.sessionStorage.setItem(key, value)
    return true
  } catch {
    return false
  }
}

function removeSessionFlag(key: string): void {
  if (typeof window === 'undefined') return

  try {
    window.sessionStorage.removeItem(key)
  } catch {
    return
  }
}

export function resetAuthOneShotFlags(): void {
  removeSessionFlag(ONBOARDING_DISMISSED_FLAG)
  removeSessionFlag(SUPER_USER_WELCOMED_FLAG)
}

export function isOnboardingDismissed(): boolean {
  return readSessionFlag(ONBOARDING_DISMISSED_FLAG) === '1'
}

export function dismissOnboarding(): void {
  writeSessionFlag(ONBOARDING_DISMISSED_FLAG, '1')
}

export function claimSuperUserWelcomeToast(): boolean {
  if (readSessionFlag(SUPER_USER_WELCOMED_FLAG) === '1') return false
  return writeSessionFlag(SUPER_USER_WELCOMED_FLAG, '1')
}
