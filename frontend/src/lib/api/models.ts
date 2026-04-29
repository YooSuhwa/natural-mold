// Read-only model catalog. Greenfield: model rows are reference data; LLM API
// keys live in Credentials. Admin CRUD intentionally absent in M6.

import { apiFetch } from './client'

export interface ModelCatalogEntry {
  id: string
  provider: string
  model_name: string
  display_name: string
  base_url: string | null
  is_default: boolean
  context_window: number | null
  input_modalities: string[] | null
  output_modalities: string[] | null
}

export const modelsApi = {
  list: () => apiFetch<ModelCatalogEntry[]>('/api/models'),
  get: (id: string) => apiFetch<ModelCatalogEntry>(`/api/models/${id}`),
}
