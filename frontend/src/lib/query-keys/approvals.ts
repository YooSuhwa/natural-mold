import type { RunState } from '@/lib/types/approval'

/** Dropped on an organization switch (not retained): the inbox is per organization. */
export const approvalQueryKeys = {
  all: ['approvals'] as const,
  list: (status: 'pending' | 'done') => ['approvals', 'list', status] as const,
  pendingCount: ['approvals', 'pending-count'] as const,
  runs: (state: RunState) => ['approvals', 'runs', state] as const,
  stops: (active: boolean) => ['approvals', 'stops', active] as const,
}
