'use client'

import { useQuery } from '@tanstack/react-query'
import { usageApi } from '@/lib/api/usage'
import { usageQueryKeys } from '@/lib/query-keys/usage'
import type { UsageDailyParams } from '@/lib/types'

export function useUsageSummary(period?: string) {
  return useQuery({
    queryKey: usageQueryKeys.summary(period),
    queryFn: () => usageApi.summary(period),
    staleTime: 60_000,
  })
}

/**
 * Daily aggregate fetcher (M10).
 *
 * Cache key includes the full params object so different filter combinations
 * are memoised separately. `staleTime` matches the summary hook so the page
 * does not over-fetch when filters toggle quickly.
 */
export function useDailyAggregate(params: UsageDailyParams) {
  return useQuery({
    queryKey: usageQueryKeys.daily(params),
    queryFn: () => usageApi.daily(params),
    staleTime: 60_000,
  })
}
