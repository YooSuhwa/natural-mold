'use client'

import { useInfiniteQuery } from '@tanstack/react-query'

import { auditApi } from '@/lib/api/audit'
import type { AuditEventListParams } from '@/lib/types/audit'

export const auditKeys = {
  root: ['audit-events'] as const,
  list: (filters: AuditEventListParams) => ['audit-events', filters] as const,
}

export function useAuditEvents(filters: AuditEventListParams) {
  return useInfiniteQuery({
    queryKey: auditKeys.list(filters),
    initialPageParam: filters.cursor ?? null,
    queryFn: ({ pageParam }) =>
      auditApi.listEvents({
        ...filters,
        cursor: pageParam,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    staleTime: 15_000,
  })
}
