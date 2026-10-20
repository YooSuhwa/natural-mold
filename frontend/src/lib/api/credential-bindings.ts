import { apiFetch } from './client'
import type {
  BindableResourceType,
  CredentialBinding,
  CredentialMode,
  CredentialModeResult,
} from '@/lib/types/readiness'

const BASE = '/api/credential-bindings'

function path(resourceType: BindableResourceType, resourceId: string): string {
  return `${BASE}/${resourceType}/${encodeURIComponent(resourceId)}`
}

/** Per-user connections (plan 9.x `CRUD /api/credential-bindings`, S23; F-04). */
export const credentialBindingsApi = {
  list: (resourceType?: BindableResourceType) =>
    apiFetch<CredentialBinding[]>(resourceType ? `${BASE}?resource_type=${resourceType}` : BASE),
  bind: (resourceType: BindableResourceType, resourceId: string, credentialId: string) =>
    apiFetch<CredentialBinding>(path(resourceType, resourceId), {
      method: 'PUT',
      body: JSON.stringify({ credential_id: credentialId }),
    }),
  unbind: (resourceType: BindableResourceType, resourceId: string) =>
    apiFetch<void>(path(resourceType, resourceId), { method: 'DELETE' }),
  /** Owner/editor switch between the resource's account and per-user connections (F-04). */
  setMode: (
    resourceType: BindableResourceType,
    resourceId: string,
    mode: CredentialMode,
    definitionKey?: string | null,
  ) =>
    apiFetch<CredentialModeResult>(
      `/api/${resourceType === 'tool' ? 'tools' : 'mcp-servers'}/${encodeURIComponent(resourceId)}/credential-mode`,
      {
        method: 'PUT',
        body: JSON.stringify({
          credential_mode: mode,
          ...(definitionKey ? { credential_definition_key: definitionKey } : {}),
        }),
      },
    ),
}
