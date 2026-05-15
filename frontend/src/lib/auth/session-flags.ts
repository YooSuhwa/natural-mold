/**
 * sessionStorage flag keys for auth-related one-shot UI events.
 *
 * Centralized so register/login mutation, OnboardingDialog, and the
 * super-user welcome toast hook agree on the exact keys. Renaming a
 * flag here resets the dismissed state for every user — handle with care.
 */

export const ONBOARDING_DISMISSED_FLAG = 'moldy.onboarding_dismissed'
export const SUPER_USER_WELCOMED_FLAG = 'moldy.super_user_welcomed'
