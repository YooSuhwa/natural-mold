// Model catalog domain types — mirrors backend `app/schemas/model.py`.
// M7: extended with `source` (litellm/openrouter/manual), `agent_count`,
// `max_output_tokens`, capability flags, and discovery payload.

export type ModelSource = 'litellm' | 'openrouter' | 'manual'

export interface Model {
  id: string
  provider: string
  model_name: string
  display_name: string
  base_url: string | null
  is_default: boolean
  cost_per_input_token: number | null
  cost_per_output_token: number | null
  context_window: number | null
  max_output_tokens: number | null
  input_modalities: string[] | null
  output_modalities: string[] | null
  supports_vision: boolean | null
  supports_function_calling: boolean | null
  supports_reasoning: boolean | null
  source: ModelSource | null
  agent_count: number
  created_at: string
}

export interface DiscoveredModel {
  model_name: string
  display_name: string
  source: ModelSource
  provider: string
  context_window: number | null
  max_output_tokens: number | null
  cost_per_input_token: number | null
  cost_per_output_token: number | null
  input_modalities: string[] | null
  output_modalities: string[] | null
  supports_vision: boolean | null
  supports_function_calling: boolean | null
  supports_reasoning: boolean | null
  already_registered: boolean
}

export interface ModelCreate {
  provider: string
  model_name: string
  display_name: string
  base_url?: string | null
  cost_per_input_token?: number | null
  cost_per_output_token?: number | null
  context_window?: number | null
  max_output_tokens?: number | null
  input_modalities?: string[] | null
  output_modalities?: string[] | null
  supports_vision?: boolean | null
  supports_function_calling?: boolean | null
  supports_reasoning?: boolean | null
  source?: ModelSource | null
  is_default?: boolean
}

export interface ModelUpdate {
  display_name?: string
  base_url?: string | null
  cost_per_input_token?: number | null
  cost_per_output_token?: number | null
  context_window?: number | null
  max_output_tokens?: number | null
  input_modalities?: string[] | null
  output_modalities?: string[] | null
  supports_vision?: boolean | null
  supports_function_calling?: boolean | null
  supports_reasoning?: boolean | null
  is_default?: boolean
}

/**
 * Discriminated union returned by `<ModelSelect />` — the parent decides how to
 * persist Custom-ID picks (e.g. as agent.model_id="custom:openai:gpt-x").
 */
export type ModelPick =
  | { mode: 'list'; model_id: string }
  | { mode: 'custom'; provider: string; model_name: string }
