'use client'

import { useEffect } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { SUPER_USER_WELCOMED_FLAG } from '@/lib/auth/session-flags'
import type { User } from '@/lib/types/user'

/**
 * Fires `toast.success(...)` exactly once per browser tab/session for a
 * brand-new super_user account. The effect only contains imperative side
 * effects (toast call + sessionStorage write) — no React state sync — so it
 * stays clear of `react-hooks/set-state-in-effect`.
 */
export function useSuperUserWelcomeToast(user: User) {
  const t = useTranslations('auth.onboarding')

  useEffect(() => {
    if (!user.is_super_user) return
    if (typeof window === 'undefined') return

    const createdAt = user.created_at ? new Date(user.created_at).getTime() : 0
    const fresh = Date.now() - createdAt < 5 * 60 * 1000
    if (!fresh) return

    try {
      const shown = sessionStorage.getItem(SUPER_USER_WELCOMED_FLAG)
      if (shown === '1') return
      sessionStorage.setItem(SUPER_USER_WELCOMED_FLAG, '1')
    } catch {
      // sessionStorage unavailable — skip.
      return
    }

    toast.success(t('superUserToast'), {
      description: t('superUserToastDesc'),
      duration: 6000,
    })
  }, [user.is_super_user, user.created_at, t])
}
