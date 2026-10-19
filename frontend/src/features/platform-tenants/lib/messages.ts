'use client'

import { useTranslations } from 'next-intl'

import { useApiErrorMessage } from '@/components/shared/api-error-state'
import { ApiError } from '@/lib/api/client'
import { formatDisplayUsd } from '@/lib/utils/display-format'

const TENANT_ERROR_CODES = [
  'stale_version',
  'duplicate_tenant_slug',
  'tenant_namespace_collision',
  'tenant_deletion_slug_mismatch',
  'tenant_export_in_progress',
  'tenant_status_conflict',
] as const

type TenantErrorCode = (typeof TENANT_ERROR_CODES)[number]

function isTenantErrorCode(code: string): code is TenantErrorCode {
  return (TENANT_ERROR_CODES as readonly string[]).includes(code)
}

/** Toast/inline copy for operator company mutations; never echoes raw server text. */
export function useTenantErrorMessage(): (error: unknown) => string {
  const t = useTranslations('appSettings.platformTenants.errors')
  const shared = useApiErrorMessage()
  return (error) => {
    if (error instanceof ApiError && isTenantErrorCode(error.code)) return t(error.code)
    return shared(error, t('generic'))
  }
}

const DAY_MS = 24 * 60 * 60 * 1000

/** Whole days left until `due` (never negative): the S04 "삭제 예정(D-n)" badge. */
export function daysUntil(due: string, now: number = Date.now()): number {
  return Math.max(0, Math.ceil((new Date(due).getTime() - now) / DAY_MS))
}

/** Decimal strings from the API as numbers; `null` stays "no limit". */
export function toAmount(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null
  const amount = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(amount) ? amount : null
}

export const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/

/** Spend amounts (USD, like the usage screens); `null` renders `none`. */
export function formatMoney(value: number | null, none: string): string {
  return value === null ? none : formatDisplayUsd(value, { maximumFractionDigits: 2 })
}
