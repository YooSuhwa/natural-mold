'use client'

import { useRouter } from 'next/navigation'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { authApi, type LoginPayload, type RegisterPayload } from '@/lib/api/auth'
import { clearCsrfToken, setCsrfToken } from '@/lib/auth/csrf'
import { SESSION_QUERY_KEY } from '@/lib/auth/session'
import type { AuthResponse } from '@/lib/types/user'

const ONBOARDING_FLAG = 'moldy.onboarding_dismissed'
const SUPER_USER_TOAST_FLAG = 'moldy.super_user_welcomed'

function isSafeCallback(url: string | null | undefined): url is string {
  if (!url) return false
  return url.startsWith('/') && !url.startsWith('//')
}

function persistAuth(res: AuthResponse, queryClient: ReturnType<typeof useQueryClient>) {
  setCsrfToken(res.csrf_token)
  queryClient.setQueryData(SESSION_QUERY_KEY, res.user)
  queryClient.invalidateQueries({ queryKey: SESSION_QUERY_KEY })
}

/** POST /api/auth/login → set CSRF, prime session cache, redirect to callbackUrl or /. */
export function useLogin() {
  const router = useRouter()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (payload: LoginPayload) => authApi.login(payload),
    onSuccess: (res) => {
      persistAuth(res, queryClient)
      const params =
        typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null
      const callback = params?.get('callbackUrl')
      router.push(isSafeCallback(callback) ? callback : '/')
    },
  })
}

/** POST /api/auth/register → set CSRF, mark onboarding pending, redirect to /. */
export function useRegister() {
  const router = useRouter()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (payload: RegisterPayload) => authApi.register(payload),
    onSuccess: (res) => {
      persistAuth(res, queryClient)
      // Reset onboarding so the dashboard dialog fires once.
      if (typeof window !== 'undefined') {
        try {
          sessionStorage.removeItem(ONBOARDING_FLAG)
          sessionStorage.removeItem(SUPER_USER_TOAST_FLAG)
        } catch {
          // sessionStorage unavailable (private mode) — silently degrade.
        }
      }
      router.push('/')
    },
  })
}

/** POST /api/auth/logout → clear cache + CSRF, redirect /login. */
export function useLogout() {
  const router = useRouter()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => authApi.logout(),
    // Always clear local state, even if the network call fails — server will
    // GC the orphaned refresh row eventually, and the user expects to be out.
    onSettled: () => {
      clearCsrfToken()
      queryClient.clear()
      router.push('/login')
    },
  })
}
