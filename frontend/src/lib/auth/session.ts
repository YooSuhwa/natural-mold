'use client'

import { useQuery } from '@tanstack/react-query'

import { authApi } from '@/lib/api/auth'
import { ApiError } from '@/lib/api/client'
import type { User } from '@/lib/types/user'

export const SESSION_QUERY_KEY = ['auth', 'session'] as const

/**
 * Fetch the current user via `GET /api/auth/me`.
 *
 * - 401 → returns `null` (treated as "logged out", not an error).
 * - Other errors propagate to the caller's error boundary.
 * - `staleTime: 5min` so route changes don't refetch aggressively.
 */
export function useSession() {
  return useQuery<User | null>({
    queryKey: SESSION_QUERY_KEY,
    queryFn: async () => {
      try {
        return await authApi.me()
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null
        throw err
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
}
