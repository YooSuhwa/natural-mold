'use client'

import { useQuery } from '@tanstack/react-query'

import { systemStatusApi } from '@/lib/api/system-status'
import { systemStatusQueryKeys } from '@/lib/query-keys/system-status'

/** Re-checked every 30 s while the screen is open; the checks probe live services. */
export function useSystemStatus() {
  return useQuery({
    queryKey: systemStatusQueryKeys.all,
    queryFn: systemStatusApi.get,
    refetchInterval: 30_000,
    staleTime: 10_000,
  })
}
