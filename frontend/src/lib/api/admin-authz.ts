import { apiFetch } from './client'
import type {
  AuthzOpsStatus,
  OutboxFailure,
  OutboxFailurePage,
  ReconcileRunView,
} from '@/lib/types/admin-authz'

const BASE = '/api/admin/authz'

/** Operator sync API (plan 14.3, S27; H-04): super user only, platform scope. */
export const adminAuthzApi = {
  status: () => apiFetch<AuthzOpsStatus>(`${BASE}/status`),
  failures: (limit = 50) =>
    apiFetch<OutboxFailurePage>(
      `${BASE}/failures?${new URLSearchParams({ limit: String(limit) })}`,
    ),
  retry: (id: string) =>
    apiFetch<OutboxFailure>(`${BASE}/failures/${encodeURIComponent(id)}/retry`, {
      method: 'POST',
    }),
  reconcile: () => apiFetch<{ job_id: string }>(`${BASE}/reconcile`, { method: 'POST' }),
  run: (id: string) => apiFetch<ReconcileRunView>(`${BASE}/reconcile/${encodeURIComponent(id)}`),
}
