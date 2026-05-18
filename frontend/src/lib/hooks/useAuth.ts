'use client'

import { useRouter } from 'next/navigation'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { authApi, type LoginPayload, type RegisterPayload } from '@/lib/api/auth'
import {
  resetRefreshBackoff,
  resetSessionExpiredFlag,
} from '@/lib/api/client'
import { csrfStore } from '@/lib/auth/csrf'
import { SESSION_QUERY_KEY } from '@/lib/auth/session'
import {
  ONBOARDING_DISMISSED_FLAG,
  SUPER_USER_WELCOMED_FLAG,
} from '@/lib/auth/session-flags'
import type { AuthResponse } from '@/lib/types/user'

function isSafeCallback(url: string | null | undefined): url is string {
  if (!url) return false
  return url.startsWith('/') && !url.startsWith('//')
}

function persistAuth(res: AuthResponse, queryClient: ReturnType<typeof useQueryClient>) {
  csrfStore.set(res.csrf_token)
  // New session — re-arm the refresh + session-expired gates so the next
  // expiry can fire toast/redirect normally.
  resetRefreshBackoff()
  resetSessionExpiredFlag()
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
          sessionStorage.removeItem(ONBOARDING_DISMISSED_FLAG)
          sessionStorage.removeItem(SUPER_USER_WELCOMED_FLAG)
        } catch {
          // sessionStorage unavailable (private mode) — silently degrade.
        }
      }
      router.push('/')
    },
  })
}

/** POST /api/auth/logout → clear cache + CSRF, full reload to /login. */
export function useLogout() {
  const router = useRouter()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => authApi.logout(),
    // Cancel in-flight queries before the call so a stray useSession or
    // useAgents refetch doesn't race with the cookie clear and produce a
    // post-logout 401 → toast/redirect storm.
    onMutate: async () => {
      resetRefreshBackoff()
      await queryClient.cancelQueries()
    },
    // Always clear local state, even if the network call fails — server will
    // GC the orphaned refresh row eventually, and the user expects to be out.
    onSettled: () => {
      csrfStore.clear()
      resetRefreshBackoff()
      resetSessionExpiredFlag()
      queryClient.clear()
      // Hard reload — same ``AppLayout``/``AppSidebar`` shell is shared
      // between authenticated routes and ``/login``, so an SPA push leaves
      // useSession/useAgents hooks mounted, which immediately refetch with
      // a now-empty cookie jar and trip the 401 → refresh → toast chain.
      // ``window.location.href`` tears the React tree down cleanly.
      if (typeof window !== 'undefined') {
        window.location.href = '/login'
      } else {
        router.push('/login')
      }
    },
  })
}
