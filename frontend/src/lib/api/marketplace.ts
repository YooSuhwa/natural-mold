// Marketplace API client. Wraps `app/routers/marketplace.py` endpoints.
// Authentication + CSRF handled by `apiFetch`.

import { apiFetch } from './client'
import type {
  CredentialRequirement,
  InstallMarketplaceItemBody,
  KSkillSyncStatus,
  MarketplaceInstallation,
  MarketplaceItem,
  MarketplaceItemACLBody,
  MarketplaceItemPatchBody,
  MarketplaceListFilters,
  MarketplaceVersionDetail,
  MarketplaceVersionSummary,
  PublishNewVersionBody,
  PublishSkillBody,
  SkillCredentialBinding,
  UpdateInstallationBody,
} from '@/lib/types/marketplace'

function buildSearch(filters?: MarketplaceListFilters): string {
  if (!filters) return ''
  const params = new URLSearchParams()
  const {
    resource_type,
    q,
    visibility,
    category,
    installed,
    install_state,
    support_level,
    source_kind,
    is_listed,
    limit,
    offset,
  } = filters
  if (resource_type) params.set('resource_type', resource_type)
  if (q) params.set('q', q)
  if (visibility) visibility.forEach((v) => params.append('visibility', v))
  if (category) category.forEach((c) => params.append('category', c))
  if (typeof installed === 'boolean') params.set('installed', String(installed))
  if (install_state) params.set('install_state', install_state)
  if (support_level) params.set('support_level', support_level)
  if (source_kind) params.set('source_kind', source_kind)
  if (typeof is_listed === 'boolean') params.set('is_listed', String(is_listed))
  if (typeof limit === 'number') params.set('limit', String(limit))
  if (typeof offset === 'number') params.set('offset', String(offset))
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export const marketplaceApi = {
  // ---- Catalog ----
  list: (filters?: MarketplaceListFilters) =>
    apiFetch<MarketplaceItem[]>(`/api/marketplace/items${buildSearch(filters)}`),

  get: (itemId: string) =>
    apiFetch<MarketplaceItem>(`/api/marketplace/items/${itemId}`),

  listVersions: (itemId: string) =>
    apiFetch<MarketplaceVersionSummary[]>(
      `/api/marketplace/items/${itemId}/versions`,
    ),

  getVersion: (versionId: string) =>
    apiFetch<MarketplaceVersionDetail>(
      `/api/marketplace/versions/${versionId}`,
    ),

  // ---- Install / update / uninstall ----
  install: (itemId: string, body: InstallMarketplaceItemBody) =>
    apiFetch<MarketplaceInstallation>(
      `/api/marketplace/items/${itemId}/install`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  updateInstallation: (installationId: string, body: UpdateInstallationBody) =>
    apiFetch<MarketplaceInstallation>(
      `/api/marketplace/installations/${installationId}/update`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  deleteInstallation: (installationId: string, deleteResource = false) =>
    apiFetch<void>(
      `/api/marketplace/installations/${installationId}?delete_resource=${deleteResource}`,
      { method: 'DELETE' },
    ),

  // ---- Publish / manage ----
  publishFromSkill: (skillId: string, body: PublishSkillBody) =>
    apiFetch<MarketplaceItem>(
      `/api/marketplace/items/from-skill/${skillId}`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  publishNewVersion: (
    itemId: string,
    skillId: string,
    body: PublishNewVersionBody,
  ) =>
    apiFetch<MarketplaceItem>(
      `/api/marketplace/items/${itemId}/versions/from-skill/${skillId}`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  patchItem: (itemId: string, body: MarketplaceItemPatchBody) =>
    apiFetch<MarketplaceItem>(`/api/marketplace/items/${itemId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  replaceACL: (itemId: string, body: MarketplaceItemACLBody) =>
    apiFetch<MarketplaceItem>(`/api/marketplace/items/${itemId}/acl`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  removeACLEntry: (itemId: string, userId: string) =>
    apiFetch<void>(`/api/marketplace/items/${itemId}/acl/${userId}`, {
      method: 'DELETE',
    }),

  disableItem: (itemId: string) =>
    apiFetch<MarketplaceItem>(`/api/marketplace/items/${itemId}/disable`, {
      method: 'POST',
    }),

  // ---- Admin (super_user) ----
  // The dedicated `/admin/items/{id}/listed` and `/admin/items/{id}/disable`
  // endpoints don't exist yet in M2.5 — moderation reuses the public list
  // with `?is_listed=false&visibility=public` (super_user sees pending
  // public items there). Listing approval flips `is_listed` via the
  // existing item PATCH path (super_user only).
  kSkillSyncStatus: () =>
    apiFetch<KSkillSyncStatus>('/api/marketplace/admin/k-skill/sync', {
      method: 'POST',
    }),

  moderationQueue: () =>
    apiFetch<MarketplaceItem[]>(
      '/api/marketplace/items?is_listed=false&visibility=public',
    ),
}

// ---------------------------------------------------------------------------
// Skill credential bindings (per-skill — ADR-017 Slice D, lives under /api/skills)
// ---------------------------------------------------------------------------

export const skillCredentialApi = {
  listRequirements: (skillId: string) =>
    apiFetch<CredentialRequirement[]>(
      `/api/skills/${skillId}/credential-requirements`,
    ),

  listBindings: (skillId: string) =>
    apiFetch<SkillCredentialBinding[]>(
      `/api/skills/${skillId}/credential-bindings`,
    ),

  setBinding: (skillId: string, requirementKey: string, credentialId: string) =>
    apiFetch<SkillCredentialBinding>(
      `/api/skills/${skillId}/credential-bindings/${encodeURIComponent(requirementKey)}`,
      {
        method: 'PUT',
        body: JSON.stringify({ credential_id: credentialId }),
      },
    ),

  deleteBinding: (skillId: string, requirementKey: string) =>
    apiFetch<void>(
      `/api/skills/${skillId}/credential-bindings/${encodeURIComponent(requirementKey)}`,
      { method: 'DELETE' },
    ),
}
