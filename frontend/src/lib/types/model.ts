// Model catalog domain types — mirrors backend `app/schemas/model.py`.
// M7: extended with `source` (litellm/openrouter/manual), `agent_count`,
// `max_output_tokens`, capability flags, and discovery payload.
// M11: adds `rankings` (LMArena ELO, LiveBench, Artificial Analysis Index)
// surfaced from the catalog enrichment cron.

export type ModelSource = 'litellm' | 'openrouter' | 'manual'

/**
 * External benchmark ranking snapshot for a model. Backend cron refreshes
 * these every ~6h. All values may be missing for new/private/Custom-ID
 * models that haven't been matched to a public benchmark yet.
 */
export interface ModelRankings {
  /** LMArena Chatbot Arena ELO (higher = better, e.g. 1234). */
  lmarena?: number
  /** LiveBench score 0–100 (higher = better, e.g. 78.2). */
  livebench?: number
  /** Artificial Analysis Intelligence Index 0–100 (higher = better). */
  aa_index?: number
}

export interface Model {
  id: string
  provider: string
  model_name: string
  display_name: string
  base_url: string | null
  is_default: boolean
  /**
   * Operator-managed visibility. `false` rows are filtered out of the
   * default `GET /api/models` and the agent-creation selector, but survive
   * for agents that already reference the row by `model_id`.
   */
  is_visible: boolean
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
  default_credential_id: string | null
  agent_count: number
  rankings: ModelRankings | null
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
  rankings: ModelRankings | null
}

export type ModelRankingKey = keyof ModelRankings

/** Sort options forwarded to `GET /api/models?sort_by=...&order=...`. */
export type ModelSortKey = ModelRankingKey | 'display_name'
export type ModelSortOrder = 'asc' | 'desc'

export interface ListModelsOptions {
  sort_by?: ModelSortKey
  order?: ModelSortOrder
  /** Super-user only — surface rows where `is_visible=false`. */
  include_hidden?: boolean
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
  is_visible?: boolean
  default_credential_id?: string | null
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
  is_visible?: boolean
  default_credential_id?: string | null
}

/**
 * Discriminated union returned by `<ModelSelect />` — the parent decides how to
 * persist Custom-ID picks (e.g. as agent.model_id="custom:openai:gpt-x").
 */
export type ModelPick =
  | { mode: 'list'; model_id: string }
  | { mode: 'custom'; provider: string; model_name: string }

// -- M8: Model connection test ----------------------------------------------

export type ModelTestErrorKind = 'auth' | 'not_found' | 'rate_limit' | 'timeout' | 'other'

export interface ModelTestError {
  kind: ModelTestErrorKind
  message: string
  raw: string | null
}

export interface ModelTestRawRequest {
  url: string
  method: string
  headers: Record<string, string>
  body: unknown
}

export interface ModelTestRawResponse {
  status_code: number
  headers: Record<string, string>
  body: unknown
}

export interface ModelTestResponse {
  success: boolean
  response: string | null
  latency_ms: number
  tokens_in: number | null
  tokens_out: number | null
  estimated_cost_usd: number | null
  error: ModelTestError | null
  raw_request: ModelTestRawRequest | null
  raw_response: ModelTestRawResponse | null
  curl_command: string | null
}

export interface ModelTestPreviewRequest {
  provider: string
  model_name: string
  base_url?: string | null
  credential_id: string
}
