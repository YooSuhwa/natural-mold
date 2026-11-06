import { apiFetch } from './client'
import type {
  ApprovalPage,
  ApprovalView,
  EmergencyStopView,
  InboxDecision,
  RunState,
  RunStatusView,
  StopScope,
} from '@/lib/types/approval'

/** Approval inbox API (plan 17.2): the caller's requests, decided once. */
export const approvalsApi = {
  list: (status: 'pending' | 'done') => apiFetch<ApprovalPage>(`/api/approvals?status=${status}`),
  pendingCount: () => apiFetch<{ count: number }>('/api/approvals/pending-count'),
  decide: (id: string, decisions: InboxDecision[]) =>
    apiFetch<ApprovalView>(`/api/approvals/${encodeURIComponent(id)}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decisions }),
    }),
  runs: (state: RunState) =>
    apiFetch<{ items: RunStatusView[] }>(`/api/approvals/runs?state=${state}`),
}

/** Emergency stop API (plan 17.4). */
export const emergencyStopsApi = {
  list: (active: boolean) =>
    apiFetch<{ items: EmergencyStopView[] }>(`/api/emergency-stops?active=${active}`),
  stop: (scope_type: StopScope, scope_id: string, reason: string) =>
    apiFetch<EmergencyStopView>('/api/emergency-stops', {
      method: 'POST',
      body: JSON.stringify({ scope_type, scope_id, reason }),
    }),
  release: (id: string, reason: string) =>
    apiFetch<EmergencyStopView>(`/api/emergency-stops/${encodeURIComponent(id)}/release`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
}
