'use client'

import { useTranslations } from 'next-intl'

import { ApiError } from '@/lib/api/errors'

export const DATE_TIME = { dateStyle: 'medium', timeStyle: 'short' } as const

const ERROR_CODES = ['authz_reconcile_running', 'authz_sync_disabled', 'not_found'] as const
type ErrorCode = (typeof ERROR_CODES)[number]

function isKnown(code: string): code is ErrorCode {
  return (ERROR_CODES as readonly string[]).includes(code)
}

/** Localized copy for the S27 action errors; never echoes raw server text. */
export function useAuthzErrorMessage(): (error: unknown) => string {
  const t = useTranslations('appSettings.authzOps.errors')
  return (error) =>
    error instanceof ApiError && isKnown(error.code) ? t(error.code) : t('generic')
}
