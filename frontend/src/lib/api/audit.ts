import { apiFetch } from './client'
import type { AuditEventListParams, AuditEventPage } from '@/lib/types/audit'

function appendParam(params: URLSearchParams, key: string, value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return
  params.set(key, String(value))
}

export const auditApi = {
  listEvents: (filters: AuditEventListParams = {}) => {
    const params = new URLSearchParams()
    appendParam(params, 'scope', filters.scope ?? 'mine')
    appendParam(params, 'limit', filters.limit ?? 50)
    appendParam(params, 'cursor', filters.cursor)
    appendParam(params, 'action', filters.action)
    appendParam(params, 'target_type', filters.target_type)
    appendParam(params, 'outcome', filters.outcome)
    appendParam(params, 'actor_user_id', filters.actor_user_id)
    appendParam(params, 'owner_user_id', filters.owner_user_id)
    appendParam(params, 'request_id', filters.request_id)
    appendParam(params, 'trace_id', filters.trace_id)
    appendParam(params, 'run_id', filters.run_id)
    appendParam(params, 'created_from', filters.created_from)
    appendParam(params, 'created_to', filters.created_to)
    const query = params.toString()
    return apiFetch<AuditEventPage>(`/api/audit-events${query ? `?${query}` : ''}`)
  },
}
