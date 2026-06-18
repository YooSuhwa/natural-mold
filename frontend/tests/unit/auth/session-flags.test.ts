import { beforeEach, describe, expect, it } from 'vitest'

import {
  claimSuperUserWelcomeToast,
  dismissOnboarding,
  isOnboardingDismissed,
  resetAuthOneShotFlags,
} from '@/lib/auth/session-flags'

describe('session-flags', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
  })

  it('tracks onboarding dismissal for the current session', () => {
    expect(isOnboardingDismissed()).toBe(false)

    dismissOnboarding()

    expect(isOnboardingDismissed()).toBe(true)
  })

  it('claims the super-user welcome toast once per session', () => {
    expect(claimSuperUserWelcomeToast()).toBe(true)
    expect(claimSuperUserWelcomeToast()).toBe(false)
  })

  it('resets auth one-shot flags', () => {
    dismissOnboarding()
    claimSuperUserWelcomeToast()

    resetAuthOneShotFlags()

    expect(isOnboardingDismissed()).toBe(false)
    expect(claimSuperUserWelcomeToast()).toBe(true)
  })
})
