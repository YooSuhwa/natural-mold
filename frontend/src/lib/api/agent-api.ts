import { apiFetch } from './client'
import type {
  AgentApiKey,
  AgentApiKeyCreateRequest,
  AgentApiKeyCreated,
  AgentDeployment,
  AgentDeploymentCandidate,
  AgentDeploymentCreateRequest,
} from '@/lib/types'

export const agentApi = {
  listDeploymentCandidates: () =>
    apiFetch<AgentDeploymentCandidate[]>('/api/agent-api/deployment-candidates'),
  listDeployments: () => apiFetch<AgentDeployment[]>('/api/agent-api/deployments'),
  createDeployment: (data: AgentDeploymentCreateRequest) =>
    apiFetch<AgentDeployment>('/api/agent-api/deployments', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateDeployment: (id: string, data: Partial<AgentDeployment>) =>
    apiFetch<AgentDeployment>(`/api/agent-api/deployments/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  listKeys: () => apiFetch<AgentApiKey[]>('/api/agent-api/keys'),
  createKey: (data: AgentApiKeyCreateRequest) =>
    apiFetch<AgentApiKeyCreated>('/api/agent-api/keys', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  revokeKey: (id: string) =>
    apiFetch<AgentApiKey>(`/api/agent-api/keys/${id}/revoke`, { method: 'POST' }),
}
