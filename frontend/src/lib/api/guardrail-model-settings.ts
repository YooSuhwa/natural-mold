import { apiFetch } from './client'
import type {
  GuardrailModelProbeOut,
  GuardrailModelProbeRequest,
  GuardrailModelSettingOut,
  GuardrailModelSettingUpdate,
} from '@/lib/types/guardrail-model-setting'

// Operator-only (super_user). Mutations need CSRF — `apiFetch` injects `X-CSRF-Token`.
const BASE = '/api/guardrail-model-settings'

export const guardrailModelSettingsApi = {
  get: () => apiFetch<GuardrailModelSettingOut>(BASE),
  update: (data: GuardrailModelSettingUpdate) =>
    apiFetch<GuardrailModelSettingOut>(BASE, { method: 'PUT', body: JSON.stringify(data) }),
  reset: () => apiFetch<void>(BASE, { method: 'DELETE' }),
  test: (data: GuardrailModelProbeRequest) =>
    apiFetch<GuardrailModelProbeOut>(`${BASE}/test`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}
