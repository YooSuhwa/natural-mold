import { apiFetch } from './client'
import type {
  AccessList,
  GrantCreate,
  GrantList,
  GrantView,
  OwnershipTransfer,
  OwnershipTransferResult,
  ShareResourceType,
} from '@/lib/types/grant'

function resourcePath(type: ShareResourceType, id: string): string {
  return `/api/resources/${type}/${encodeURIComponent(id)}`
}

/** Share, access-list and ownership-transfer API (plan 9.7, E-01). */
export const grantsApi = {
  list: (type: ShareResourceType, id: string) =>
    apiFetch<GrantList>(`${resourcePath(type, id)}/grants`),
  create: (type: ShareResourceType, id: string, body: GrantCreate) =>
    apiFetch<GrantView>(`${resourcePath(type, id)}/grants`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  access: (type: ShareResourceType, id: string) =>
    apiFetch<AccessList>(`${resourcePath(type, id)}/access`),
  transfer: (type: ShareResourceType, id: string, body: OwnershipTransfer) =>
    apiFetch<OwnershipTransferResult>(`${resourcePath(type, id)}/transfer`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  revoke: (grantId: string) =>
    apiFetch<void>(`/api/grants/${encodeURIComponent(grantId)}`, { method: 'DELETE' }),
}
