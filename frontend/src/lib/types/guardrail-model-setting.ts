// Guardrail model service settings — mirrors backend
// `app/schemas/guardrail_model_setting.py` (guardrail-minimum-plan.md §2.1).

/** Credential definition_key the optional service key must use. */
export const GUARDRAIL_MODEL_CREDENTIAL_KEY = 'http_bearer'

export interface GuardrailModelSettingOut {
  enabled: boolean
  url: string
  /** `environment`: nothing saved, GUARDRAIL_PII_MODEL_URL applies. */
  source: 'environment' | 'saved'
  environment_url: string
  credential_id: string | null
  credential_name: string | null
  updated_at: string | null
}

export interface GuardrailModelSettingUpdate {
  enabled: boolean
  url: string
  credential_id: string | null
}

export const GUARDRAIL_MODEL_PROBE_STATUSES = [
  'ok',
  'unreachable',
  'timeout',
  'unauthorized',
  'model_missing',
  'bad_response',
] as const

export type GuardrailModelProbeStatus = (typeof GUARDRAIL_MODEL_PROBE_STATUSES)[number]

export interface GuardrailModelProbeRequest {
  url: string
  credential_id: string | null
}

export interface GuardrailModelProbeOut {
  status: GuardrailModelProbeStatus
  latency_ms: number | null
  model: string | null
  findings: number | null
}
