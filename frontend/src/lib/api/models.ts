// Model catalog client: CRUD, credential-driven discovery, and connection tests.

import { apiFetch } from './client'
import type {
  DiscoveredModel,
  ListModelsOptions,
  Model,
  ModelCreate,
  ModelTestPreviewRequest,
  ModelTestResponse,
  ModelUpdate,
} from '@/lib/types/model'

function buildListQuery(options?: ListModelsOptions): string {
  if (!options) return ''
  const params = new URLSearchParams()
  if (options.sort_by) params.set('sort_by', options.sort_by)
  if (options.order) params.set('order', options.order)
  if (options.include_hidden) params.set('include_hidden', 'true')
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export const modelsApi = {
  list: (options?: ListModelsOptions) => apiFetch<Model[]>(`/api/models${buildListQuery(options)}`),
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
  delete: (id: string) => apiFetch<void>(`/api/models/${id}`, { method: 'DELETE' }),

  /**
   * Ask the backend to enumerate models reachable through a saved credential
   * (OpenRouter, OpenAI compatible, etc.). Pricing/source metadata is enriched
   * server-side via the LiteLLM catalog or provider-native list endpoints.
   */
  discoverFromCredential: (credentialId: string) =>
    apiFetch<DiscoveredModel[]>(`/api/credentials/${credentialId}/discover-models`, {
      method: 'POST',
    }),

  /**
   * Run a one-shot completion against an already-registered model. The backend
   * decrypts the supplied credential and returns latency / tokens / cost plus
   * the cleaned error if the call fails. Authorization headers are masked
   * server-side before being echoed back in `raw_request`.
   */
  testRegistered: (modelId: string, credentialId: string) =>
    apiFetch<ModelTestResponse>(
      `/api/models/${modelId}/test?credential_id=${encodeURIComponent(credentialId)}`,
      { method: 'POST' },
    ),

  /**
   * Same as `testRegistered`, but for an in-flight form (Add/Custom ID flow)
   * where the user hasn't persisted the model yet.
   */
  testPreview: (payload: ModelTestPreviewRequest) =>
    apiFetch<ModelTestResponse>('/api/models/test-preview', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
