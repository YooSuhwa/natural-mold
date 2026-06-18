import { API_BASE } from '@/lib/api/client'
import type { User } from '@/lib/types/user'

export async function getAuthPageSession(): Promise<User | null> {
  // Raw fetch bypasses apiFetch/withAuthRetry so a 401 here does NOT trigger
  // fireSessionExpired() -> queryClient.clear(). If auth pages used useSession()
  // instead, the clear() would cancel the in-flight query, re-run it
  // indefinitely, and show a spurious "session expired" toast while already on
  // the login page.
  const res = await fetch(`${API_BASE}/api/auth/me`, { credentials: 'include' })
  if (!res.ok) return null
  return (await res.json()) as User
}
