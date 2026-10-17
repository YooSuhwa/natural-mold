import type { AccessRequestView } from '@/lib/types/access-request'

export function makeAccessRequest(overrides: Partial<AccessRequestView> = {}): AccessRequestView {
  return {
    id: 'req-1',
    resource_type: 'skill',
    resource_id: 'skill-1',
    resource_name: '영업 리포트',
    level: 'editor',
    reason: '분기 리포트 양식을 수정해야 해요.',
    status: 'pending',
    requester: { id: 'u-2', display_name: '정우진' },
    decided_by: null,
    decided_at: null,
    decision_note: null,
    granted_level: null,
    created_at: '2026-10-17T00:00:00Z',
    expires_at: new Date(Date.now() + 29.5 * 86_400_000).toISOString(),
    ...overrides,
  }
}
