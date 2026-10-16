'use client'

import { useTranslations } from 'next-intl'
import { ApiError } from '@/lib/api/errors'

const KNOWN_ERROR_CODES = [
  'stale_version',
  'invalid_cursor',
  'organization_archive_name_mismatch',
  'organization_restore_expired',
  'default_organization_archive_forbidden',
  'last_admin',
  'last_organization',
  'permission_required',
  'policy_locked',
  'conflict',
  'not_found',
  'forbidden',
] as const

type KnownErrorCode = (typeof KNOWN_ERROR_CODES)[number]

function isKnownErrorCode(code: string): code is KnownErrorCode {
  return (KNOWN_ERROR_CODES as readonly string[]).includes(code)
}

/** Map backend org-admin error codes to localized copy (never echoes raw server text). */
export function useOrgErrorMessage(): (error: unknown) => string {
  const t = useTranslations('orgAdmin.errors')
  return (error) => {
    if (error instanceof ApiError && isKnownErrorCode(error.code)) return t(error.code)
    return t('generic')
  }
}
