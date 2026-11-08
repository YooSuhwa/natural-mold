import { apiFetch } from './client'
import type {
  WorkflowDefinition,
  WorkflowDetail,
  WorkflowIssues,
  WorkflowSummary,
  WorkflowVersionSummary,
} from '@/lib/types/workflow'

const path = (id: string) => `/api/workflows/${encodeURIComponent(id)}`

/** Workflow management API (owner only, 1차 Q5). */
export const workflowsApi = {
  list: () => apiFetch<WorkflowSummary[]>('/api/workflows'),
  create: (name: string) =>
    apiFetch<WorkflowDetail>('/api/workflows', { method: 'POST', body: JSON.stringify({ name }) }),
  get: (id: string) => apiFetch<WorkflowDetail>(path(id)),
  saveDraft: (
    id: string,
    body: { revision: number; definition?: WorkflowDefinition; name?: string },
  ) => apiFetch<WorkflowDetail>(`${path(id)}/draft`, { method: 'PUT', body: JSON.stringify(body) }),
  issues: (id: string) => apiFetch<WorkflowIssues>(`${path(id)}/issues`),
  publish: (id: string, revision: number) =>
    apiFetch<WorkflowVersionSummary>(`${path(id)}/publish`, {
      method: 'POST',
      body: JSON.stringify({ revision }),
    }),
  archive: (id: string) => apiFetch<WorkflowDetail>(`${path(id)}/archive`, { method: 'POST' }),
}
