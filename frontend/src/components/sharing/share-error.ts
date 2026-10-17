import type { useTranslations } from 'next-intl'

import { ApiError } from '@/lib/api/client'

type Translate = ReturnType<typeof useTranslations>

const GRANT_INVALID_REASONS = new Set([
  'archived',
  'expired',
  'expires_at',
  'level',
  'owner',
  'principal',
  'reason_required',
  'self_grant',
  'self_transfer',
])

const SIMPLE_CODES = new Set([
  'permission_required',
  'capability_required',
  'transfer_name_conflict',
  'stale_version',
  'conflict',
  'authz_unavailable',
  'marketplace_acl_required',
  'personal_account_shared',
])

/** One user-facing sentence for a sharing API failure (plan 13.12.1 action column). */
export function shareErrorMessage(error: unknown, t: Translate): string {
  if (!(error instanceof ApiError)) return t('errors.generic')
  if (error.code === 'grant_invalid') {
    const reason = error.details?.reason
    return typeof reason === 'string' && GRANT_INVALID_REASONS.has(reason)
      ? t(`errors.grantInvalid.${reason}`)
      : t('errors.grantInvalid.default')
  }
  if (error.code === 'transfer_blocked') return t('errors.transferBlocked')
  if (SIMPLE_CODES.has(error.code)) return t(`errors.${error.code}`)
  if (error.status === 404) return t('errors.not_found')
  return t('errors.generic')
}

export { delegationsToConfirm } from '@/lib/sharing/delegations'

export interface PersonalAccountUse {
  key: string
  resourceType: string
  definitionKey: string
}

/** E-05 `personal_account_shared` → `details.personal_accounts` (one row per carrier). */
export function personalAccountUses(error: unknown): PersonalAccountUse[] {
  if (!(error instanceof ApiError) || error.code !== 'personal_account_shared') return []
  const raw = error.details?.personal_accounts
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item: unknown, index) => {
    if (!item || typeof item !== 'object') return []
    const { resource_type, resource_id, definition_key } = item as Record<string, unknown>
    if (typeof resource_type !== 'string' || typeof definition_key !== 'string') return []
    const id = typeof resource_id === 'string' ? resource_id : String(index)
    return [
      { key: `${resource_type}:${id}`, resourceType: resource_type, definitionKey: definition_key },
    ]
  })
}

export interface TransferBlocker {
  kind: 'personal_account' | 'triggers' | 'deployments' | 'api_keys' | 'credentials'
  count: number
}

const BLOCKER_KINDS: readonly TransferBlocker['kind'][] = [
  'personal_account',
  'triggers',
  'deployments',
  'api_keys',
  'credentials',
]

/** `transfer_blocked` details → what the owner has to clean up first. */
export function transferBlockers(error: unknown): TransferBlocker[] {
  if (!(error instanceof ApiError) || error.code !== 'transfer_blocked') return []
  const details = error.details ?? {}
  return BLOCKER_KINDS.flatMap((kind) => {
    const value = details[kind]
    if (Array.isArray(value) && value.length > 0) return [{ kind, count: value.length }]
    if (typeof value === 'string' && value) return [{ kind, count: 1 }]
    return []
  })
}
