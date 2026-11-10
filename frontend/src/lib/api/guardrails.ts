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
  GuardrailTemplateVersion,
  RegistrationSettings,
  RegistrationSettingsUpdate,
  ToolPolicy,
  ToolPolicyImpact,
  ToolPolicySaved,
  ToolPolicyTarget,
  ToolPolicyUpdate,
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
  listVersions: (tenantId: string, id: string) =>
    apiFetch<GuardrailTemplateVersion[]>(templatePath(tenantId, id, '/versions')),
  /** Publishes an earlier version's rules again as the next version. */
  revertTemplate: (tenantId: string, id: string, target: number, version: string) =>
    apiFetch<GuardrailTemplate>(templatePath(tenantId, id, '/revert'), {
      method: 'POST',
      headers: tenantVersionHeader(version),
      body: JSON.stringify({ version: target }),
    }),
  archiveTemplate: (tenantId: string, id: string, version: string) =>
    apiFetch<GuardrailTemplate>(templatePath(tenantId, id, '/archive'), {
      method: 'POST',
      headers: tenantVersionHeader(version),
    }),
}

const toolPolicyPath = (tenantId: string, key: string, rest = '') =>
  base(tenantId, `/tool-policies/${encodeURIComponent(key)}${rest}`)

/** Company policy per tool or MCP server (tool-governance-plan 3.4). */
export const toolPoliciesApi = {
  list: (tenantId: string) =>
    apiFetch<{ policies: ToolPolicy[] }>(base(tenantId, '/tool-policies')),
  /** What a policy can name; company admins only. */
  targets: (tenantId: string) =>
    apiFetch<ToolPolicyTarget[]>(base(tenantId, '/tool-policies/targets')),
  /** Every field empty removes the policy. */
  save: (tenantId: string, key: string, data: ToolPolicyUpdate) =>
    apiFetch<ToolPolicySaved>(toolPolicyPath(tenantId, key), {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  /** The agent settings that would stop applying; nothing is saved. */
  impact: (tenantId: string, key: string, data: ToolPolicyUpdate) =>
    apiFetch<ToolPolicyImpact>(toolPolicyPath(tenantId, key, '/impact'), {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}

/** Who may register tools, MCP servers, skills and agents (tool-governance-plan 3.6). */
export const registrationApi = {
  get: (tenantId: string) => apiFetch<RegistrationSettings>(base(tenantId, '/registration')),
  put: (tenantId: string, data: RegistrationSettingsUpdate, version: string) =>
    apiFetch<RegistrationSettings>(base(tenantId, '/registration'), {
      method: 'PUT',
      headers: tenantVersionHeader(version),
      body: JSON.stringify(data),
    }),
}

const agentPath = (agentId: string) => `/api/agents/${encodeURIComponent(agentId)}/guardrails`

/** An agent's guardrail choice (`agent#can_edit`). */
export const agentGuardrailsApi = {
  get: (agentId: string) => apiFetch<AgentGuardrails>(agentPath(agentId)),
  put: (agentId: string, data: AgentGuardrailsUpdate) =>
    apiFetch<AgentGuardrails>(agentPath(agentId), { method: 'PUT', body: JSON.stringify(data) }),
}
