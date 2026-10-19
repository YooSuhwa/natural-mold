'use client'

import { useTranslations } from 'next-intl'

import { useApiErrorMessage } from '@/components/shared/api-error-state'
import { ApiError } from '@/lib/api/errors'

const REMOVAL_CODES = [
  'stale_version',
  'last_admin',
  'resource_transfer_required',
  'transfer_blocked',
  'conflict',
] as const

const GRANT_INVALID_REASONS = ['not_owned', 'delete_unsupported', 'principal', 'self_transfer']

/** One localized sentence for a failed removal (never the raw server text). */
export function useRemovalErrorMessage(): (error: unknown) => string {
  const t = useTranslations('orgAdmin.memberRemoval.errors')
  const apiMessage = useApiErrorMessage()
  return (error) => {
    if (error instanceof ApiError) {
      if ((REMOVAL_CODES as readonly string[]).includes(error.code)) return t(error.code)
      if (error.code === 'grant_invalid') {
        const reason = error.details?.reason
        return typeof reason === 'string' && GRANT_INVALID_REASONS.includes(reason)
          ? t(`grantInvalid.${reason}`)
          : t('grantInvalid.default')
      }
    }
    return apiMessage(error, t('generic'))
  }
}
