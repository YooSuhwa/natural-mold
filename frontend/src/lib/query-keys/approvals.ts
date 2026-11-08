import type { RunState } from '@/lib/types/approval'

/** Dropped on an organization switch (not retained): the inbox is per organization. */
export const approvalQueryKeys = {
  all: ['approvals'] as const,
  list: (status: 'pending' | 'done') => ['approvals', 'list', status] as const,
  pendingCount: ['approvals', 'pending-count'] as const,
  /** The pending request a workflow run waits on, per waiting point (status + node). */
  workflowRun: (runId: string, status: string, node: string | null) =>
    ['approvals', 'workflow-run', runId, status, node] as const,
  runs: (state: RunState) => ['approvals', 'runs', state] as const,
  stops: (active: boolean) => ['approvals', 'stops', active] as const,
}
