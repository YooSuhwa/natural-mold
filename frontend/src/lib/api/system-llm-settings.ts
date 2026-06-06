import { apiFetch } from './client'
import type {
  SystemLlmRole,
  SystemLlmSettingOut,
  SystemLlmSettingUpdate,
} from '@/lib/types/system-llm-setting'

// Operator-managed System LLM role slots (ADR-019). super_user only; the PUT
// route requires CSRF — `apiFetch` injects `X-CSRF-Token` for mutations.
export const systemLlmSettingsApi = {
  list: () => apiFetch<SystemLlmSettingOut[]>('/api/system-llm-settings'),
  update: (role: SystemLlmRole, data: SystemLlmSettingUpdate) =>
    apiFetch<SystemLlmSettingOut>(`/api/system-llm-settings/${role}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
}
