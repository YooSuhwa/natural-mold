import { useTranslations } from 'next-intl'

import { ApiError } from '@/lib/api/client'

const KNOWN_ERRORS = new Set([
  'domain_invalid',
  'domain_taken',
  'domain_not_verified',
  'ldap_plaintext_refused',
  'sso_config_invalid',
  'sso_connection_not_tested',
  'sso_policy_incomplete',
  'sso_issuer_mismatch',
  'sso_discovery_failed',
  'sso_token_invalid',
  'sso_idp_error',
  'invalid_credentials',
  'sso_email_unverified',
  'ldap_unavailable',
])

/** i18n message for an S30 error code (unknown codes get the generic message). */
export function useSsoErrorText() {
  const t = useTranslations('orgAdmin.sso.errors')
  return (error: unknown): string => {
    const code = error instanceof ApiError ? error.code : typeof error === 'string' ? error : ''
    return KNOWN_ERRORS.has(code) ? t(code) : t('generic')
  }
}
