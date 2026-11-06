import { apiFetch } from './client'
import { tenantPath, tenantVersionHeader } from './tenant-admin'
import type {
  AgentGuardrails,
  AgentGuardrailsUpdate,
  GuardrailPolicy,
  GuardrailPolicyUpdate,
  GuardrailTemplate,
  GuardrailTemplateCreate,
  GuardrailTemplateUpdate,
} from '@/lib/types/guardrails'

const base = (tenantId: string, rest = '') => tenantPath(tenantId, `/guardrails${rest}`)
const templatePath = (tenantId: string, id: string, rest = '') =>
  base(tenantId, `/templates/${encodeURIComponent(id)}${rest}`)

/** Company guardrail policy and templates (guardrail-minimum-plan.md §3.4). */
export const guardrailsApi = {
  getPolicy: (tenantId: string) => apiFetch<GuardrailPolicy>(base(tenantId)),
  putPolicy: (tenantId: string, data: GuardrailPolicyUpdate, version: string) =>
    apiFetch<GuardrailPolicy>(base(tenantId), {
      method: 'PUT',
      headers: tenantVersionHeader(version),
      body: JSON.stringify(data),
    }),
  listTemplates: (tenantId: string) => apiFetch<GuardrailTemplate[]>(base(tenantId, '/templates')),
  createTemplate: (tenantId: string, data: GuardrailTemplateCreate) =>
    apiFetch<GuardrailTemplate>(base(tenantId, '/templates'), {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  /** Template writes carry the template's own version (`updated_at`). */
  updateTemplate: (tenantId: string, id: string, data: GuardrailTemplateUpdate, version: string) =>
    apiFetch<GuardrailTemplate>(templatePath(tenantId, id), {
      method: 'PATCH',
      headers: tenantVersionHeader(version),
      body: JSON.stringify(data),
    }),
  publishTemplate: (tenantId: string, id: string, version: string) =>
    apiFetch<GuardrailTemplate>(templatePath(tenantId, id, '/publish'), {
      method: 'POST',
      headers: tenantVersionHeader(version),
    }),
  archiveTemplate: (tenantId: string, id: string, version: string) =>
    apiFetch<GuardrailTemplate>(templatePath(tenantId, id, '/archive'), {
      method: 'POST',
      headers: tenantVersionHeader(version),
    }),
}

const agentPath = (agentId: string) => `/api/agents/${encodeURIComponent(agentId)}/guardrails`

/** An agent's guardrail choice (`agent#can_edit`). */
export const agentGuardrailsApi = {
  get: (agentId: string) => apiFetch<AgentGuardrails>(agentPath(agentId)),
  put: (agentId: string, data: AgentGuardrailsUpdate) =>
    apiFetch<AgentGuardrails>(agentPath(agentId), { method: 'PUT', body: JSON.stringify(data) }),
}
