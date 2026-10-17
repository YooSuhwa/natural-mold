import { apiFetch } from './client'
import { tenantPath, tenantVersionHeader } from './tenant-admin'
import type {
  CatalogOverride,
  TenantCatalogPolicy,
  TenantCredential,
  TenantCredentialCreate,
  TenantCredentialUpdate,
  TenantLlmSettingUpdate,
  TenantLlmSettings,
  TenantModel,
  TenantModelCreate,
  TenantModelList,
  TenantModelUpdate,
} from '@/lib/types/tenant-admin'

function json(method: string, body?: unknown, headers?: Record<string, string>): RequestInit {
  return { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }
}

const idPath = (tenantId: string, kind: string, id: string) =>
  tenantPath(tenantId, `/${kind}/${encodeURIComponent(id)}`)

/** S07 company models, shared credentials, service LLM and catalog (E-07). */
export const tenantCatalogApi = {
  listModels: (tenantId: string) => apiFetch<TenantModelList>(tenantPath(tenantId, '/models')),
  createModel: (tenantId: string, data: TenantModelCreate) =>
    apiFetch<TenantModel>(tenantPath(tenantId, '/models'), json('POST', data)),
  updateModel: (tenantId: string, modelId: string, data: TenantModelUpdate) =>
    apiFetch<TenantModel>(idPath(tenantId, 'models', modelId), json('PATCH', data)),
  deleteModel: (tenantId: string, modelId: string) =>
    apiFetch<void>(idPath(tenantId, 'models', modelId), json('DELETE')),

  listCredentials: (tenantId: string) =>
    apiFetch<TenantCredential[]>(tenantPath(tenantId, '/credentials')),
  createCredential: (tenantId: string, data: TenantCredentialCreate) =>
    apiFetch<TenantCredential>(tenantPath(tenantId, '/credentials'), json('POST', data)),
  updateCredential: (tenantId: string, credentialId: string, data: TenantCredentialUpdate) =>
    apiFetch<TenantCredential>(idPath(tenantId, 'credentials', credentialId), json('PATCH', data)),
  deleteCredential: (tenantId: string, credentialId: string) =>
    apiFetch<void>(idPath(tenantId, 'credentials', credentialId), json('DELETE')),

  getCatalogPolicy: (tenantId: string) =>
    apiFetch<TenantCatalogPolicy>(tenantPath(tenantId, '/catalog-policy')),
  putCatalogPolicy: (tenantId: string, items: CatalogOverride[], version: string) =>
    apiFetch<TenantCatalogPolicy>(
      tenantPath(tenantId, '/catalog-policy'),
      json('PUT', { items }, tenantVersionHeader(version)),
    ),

  getLlmSettings: (tenantId: string) =>
    apiFetch<TenantLlmSettings>(tenantPath(tenantId, '/llm-settings')),
  putLlmSetting: (tenantId: string, data: TenantLlmSettingUpdate) =>
    apiFetch<TenantLlmSettings>(tenantPath(tenantId, '/llm-settings'), json('PUT', data)),
}
