import { apiFetch } from './client'
import type {
  WorkflowAssistResponse,
  WorkflowAssistTurn,
  WorkflowDefinition,
  WorkflowDetail,
  WorkflowAgentUsage,
  WorkflowIssues,
  WorkflowRunDetail,
  WorkflowRunSummary,
  WorkflowSummary,
  WorkflowVersionDetail,
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
  assist: (
    id: string,
    body: { revision: number; message: string; history: WorkflowAssistTurn[] },
  ) =>
    apiFetch<WorkflowAssistResponse>(`${path(id)}/assist`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  publish: (id: string, revision: number) =>
    apiFetch<WorkflowVersionSummary>(`${path(id)}/publish`, {
      method: 'POST',
      body: JSON.stringify({ revision }),
    }),
  versions: (id: string) => apiFetch<WorkflowVersionSummary[]>(`${path(id)}/versions`),
  version: (id: string, versionNo: number) =>
    apiFetch<WorkflowVersionDetail>(`${path(id)}/versions/${versionNo}`),
  startRun: (id: string, inputs: Record<string, unknown>) =>
    apiFetch<WorkflowRunSummary>(`${path(id)}/runs`, {
      method: 'POST',
      body: JSON.stringify({ inputs }),
    }),
  runs: (id: string) => apiFetch<WorkflowRunSummary[]>(`${path(id)}/runs`),
  run: (runId: string) =>
    apiFetch<WorkflowRunDetail>(`/api/workflow-runs/${encodeURIComponent(runId)}`),
  cancelRun: (runId: string) =>
    apiFetch<WorkflowRunDetail>(`/api/workflow-runs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
    }),
  usingAgent: (agentId: string) =>
    apiFetch<WorkflowAgentUsage[]>(`/api/workflows/using-agent/${encodeURIComponent(agentId)}`),
  archive: (id: string) => apiFetch<WorkflowDetail>(`${path(id)}/archive`, { method: 'POST' }),
}
