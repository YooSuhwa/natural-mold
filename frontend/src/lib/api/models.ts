// Model catalog client. M7 reintroduces CRUD + credential-driven discovery
// (resourceLocator pattern: List vs Custom ID — see NOTICES.md).

import { apiFetch } from './client'
import type {
  DiscoveredModel,
  Model,
  ModelCreate,
  ModelUpdate,
} from '@/lib/types/model'

export const modelsApi = {
  list: () => apiFetch<Model[]>('/api/models'),
  get: (id: string) => apiFetch<Model>(`/api/models/${id}`),
  create: (data: ModelCreate) =>
    apiFetch<Model>('/api/models', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (id: string, data: ModelUpdate) =>
    apiFetch<Model>(`/api/models/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (id: string) =>
    apiFetch<void>(`/api/models/${id}`, { method: 'DELETE' }),

  /**
   * Ask the backend to enumerate models reachable through a saved credential
   * (OpenRouter, OpenAI compatible, etc.). Pricing/source metadata is enriched
   * server-side via the LiteLLM catalog or provider-native list endpoints.
   */
  discoverFromCredential: (credentialId: string) =>
    apiFetch<DiscoveredModel[]>(
      `/api/credentials/${credentialId}/discover-models`,
      { method: 'POST' },
    ),
}
