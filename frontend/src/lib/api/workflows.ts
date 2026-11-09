import { apiFetch } from './client'
import type {
  AgentWorkflowTool,
  WorkflowAssistResponse,
  WorkflowAssistTurn,
  WorkflowDefinition,
  WorkflowDetail,
  WorkflowAgentUsage,
  WorkflowIssues,
  WorkflowRunDetail,
  WorkflowRunSummary,
  WorkflowSchedule,
  WorkflowScheduleInput,
  WorkflowSummary,
  WorkflowWebhook,
  WorkflowWebhookCreated,
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
  runForToolCall: (conversationId: string, toolCallId: string) =>
    apiFetch<WorkflowRunDetail>(
      `/api/workflow-runs/by-tool-call?conversation_id=${encodeURIComponent(conversationId)}&tool_call_id=${encodeURIComponent(toolCallId)}`,
    ),
  agentTools: (agentId: string) =>
    apiFetch<AgentWorkflowTool[]>(`/api/agents/${encodeURIComponent(agentId)}/workflow-tools`),
  setAgentTools: (agentId: string, workflowIds: string[]) =>
    apiFetch<AgentWorkflowTool[]>(`/api/agents/${encodeURIComponent(agentId)}/workflow-tools`, {
      method: 'PUT',
      body: JSON.stringify({ workflow_ids: workflowIds }),
    }),
  usingAgent: (agentId: string) =>
    apiFetch<WorkflowAgentUsage[]>(`/api/workflows/using-agent/${encodeURIComponent(agentId)}`),
  archive: (id: string) => apiFetch<WorkflowDetail>(`${path(id)}/archive`, { method: 'POST' }),
  schedules: (id: string) => apiFetch<WorkflowSchedule[]>(`${path(id)}/schedules`),
  createSchedule: (id: string, body: WorkflowScheduleInput) =>
    apiFetch<WorkflowSchedule>(`${path(id)}/schedules`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateSchedule: (
    scheduleId: string,
    body: { schedule?: WorkflowScheduleInput; status?: WorkflowSchedule['status'] },
  ) =>
    apiFetch<WorkflowSchedule>(`/api/workflow-schedules/${encodeURIComponent(scheduleId)}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteSchedule: (scheduleId: string) =>
    apiFetch<void>(`/api/workflow-schedules/${encodeURIComponent(scheduleId)}`, {
      method: 'DELETE',
    }),
  webhooks: (id: string) => apiFetch<WorkflowWebhook[]>(`${path(id)}/webhooks`),
  createWebhook: (id: string, name: string) =>
    apiFetch<WorkflowWebhookCreated>(`${path(id)}/webhooks`, {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  rotateWebhook: (webhookId: string) =>
    apiFetch<WorkflowWebhookCreated>(
      `/api/workflow-webhooks/${encodeURIComponent(webhookId)}/token`,
      { method: 'POST' },
    ),
  updateWebhook: (webhookId: string, status: WorkflowWebhook['status']) =>
    apiFetch<WorkflowWebhook>(`/api/workflow-webhooks/${encodeURIComponent(webhookId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  deleteWebhook: (webhookId: string) =>
    apiFetch<void>(`/api/workflow-webhooks/${encodeURIComponent(webhookId)}`, {
      method: 'DELETE',
    }),
}
