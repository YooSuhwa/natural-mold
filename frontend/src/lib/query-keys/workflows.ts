/** Dropped on an organization switch: workflows belong to one organization. */
export const workflowQueryKeys = {
  all: ['workflows'] as const,
  list: ['workflows', 'list'] as const,
  detail: (id: string) => ['workflows', 'detail', id] as const,
  issues: (id: string, revision: number) => ['workflows', 'issues', id, revision] as const,
}
