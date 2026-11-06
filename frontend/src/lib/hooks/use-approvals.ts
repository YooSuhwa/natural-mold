'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { approvalsApi, emergencyStopsApi } from '@/lib/api/approvals'
import { approvalQueryKeys } from '@/lib/query-keys/approvals'
import type { InboxDecision, RunState, StopScope } from '@/lib/types/approval'

const PENDING_POLL_MS = 60_000

export function useApprovals(status: 'pending' | 'done') {
  return useQuery({
    queryKey: approvalQueryKeys.list(status),
    queryFn: () => approvalsApi.list(status),
  })
}

export function usePendingApprovalCount(enabled = true) {
  return useQuery({
    queryKey: approvalQueryKeys.pendingCount,
    queryFn: () => approvalsApi.pendingCount(),
    refetchInterval: PENDING_POLL_MS,
    enabled,
  })
}

export function useDecideApproval() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, decisions }: { id: string; decisions: InboxDecision[] }) =>
      approvalsApi.decide(id, decisions),
    onSettled: () => qc.invalidateQueries({ queryKey: approvalQueryKeys.all }),
  })
}

export function useRunStatus(state: RunState) {
  return useQuery({
    queryKey: approvalQueryKeys.runs(state),
    queryFn: () => approvalsApi.runs(state),
  })
}

export function useEmergencyStops(active: boolean) {
  return useQuery({
    queryKey: approvalQueryKeys.stops(active),
    queryFn: () => emergencyStopsApi.list(active),
  })
}

export function useEmergencyStop() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ scope, id, reason }: { scope: StopScope; id: string; reason: string }) =>
      emergencyStopsApi.stop(scope, id, reason),
    onSettled: () => qc.invalidateQueries({ queryKey: approvalQueryKeys.all }),
  })
}

export function useReleaseEmergencyStop() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      emergencyStopsApi.release(id, reason),
    onSettled: () => qc.invalidateQueries({ queryKey: approvalQueryKeys.all }),
  })
}
