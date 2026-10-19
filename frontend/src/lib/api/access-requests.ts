import { apiFetch } from './client'
import type {
  AccessRequestApprove,
  AccessRequestCreate,
  AccessRequestPage,
  AccessRequestView,
  RequestBox,
} from '@/lib/types/access-request'

const BASE = '/api/access-requests'

function decision(id: string, action: 'approve' | 'deny' | 'cancel', body: object = {}) {
  return apiFetch<AccessRequestView>(`${BASE}/${encodeURIComponent(id)}/${action}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** Access request API (plan 9.7 "접근 요청", S18; H-01). */
export const accessRequestsApi = {
  /**
   * `undefined` is the 202 answer: the server never says whether the resource exists or
   * whether a request was recorded (plan 8.4). A view means a real (or the pending) request.
   */
  create: (body: AccessRequestCreate) =>
    apiFetch<AccessRequestView | undefined>(BASE, { method: 'POST', body: JSON.stringify(body) }),
  list: (box: RequestBox, cursor?: string | null) => {
    const params = new URLSearchParams({ box })
    if (cursor) params.set('cursor', cursor)
    return apiFetch<AccessRequestPage>(`${BASE}?${params.toString()}`)
  },
  approve: (id: string, body: AccessRequestApprove = {}) => decision(id, 'approve', body),
  deny: (id: string, note?: string | null) => decision(id, 'deny', { note: note ?? null }),
  cancel: (id: string) => decision(id, 'cancel'),
}
