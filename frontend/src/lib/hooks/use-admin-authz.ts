'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { adminAuthzApi } from '@/lib/api/admin-authz'
import { adminAuthzQueryKeys } from '@/lib/query-keys/admin-authz'
import type { AuthzOpsStatus } from '@/lib/types/admin-authz'

/** S27: poll quickly while a reconcile run is live, slowly otherwise. */
export const ACTIVE_POLL_MS = 3_000
export const IDLE_POLL_MS = 30_000

export function statusPollInterval(status: AuthzOpsStatus | undefined): number {
  return status?.reconcile.active ? ACTIVE_POLL_MS : IDLE_POLL_MS
}

export function useAuthzOpsStatus() {
  return useQuery({
    queryKey: adminAuthzQueryKeys.status(),
    queryFn: adminAuthzApi.status,
    refetchInterval: (query) => statusPollInterval(query.state.data),
  })
}

export function useOutboxFailures() {
  return useQuery({
    queryKey: adminAuthzQueryKeys.failures(),
    queryFn: () => adminAuthzApi.failures(50),
    refetchInterval: IDLE_POLL_MS,
  })
}

function useRefresh() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: adminAuthzQueryKeys.all })
}

export function useRetryOutboxFailure() {
  const refresh = useRefresh()
  return useMutation({ mutationFn: (id: string) => adminAuthzApi.retry(id), onSuccess: refresh })
}

export function useStartReconcile() {
  const refresh = useRefresh()
  return useMutation({ mutationFn: () => adminAuthzApi.reconcile(), onSettled: refresh })
}
