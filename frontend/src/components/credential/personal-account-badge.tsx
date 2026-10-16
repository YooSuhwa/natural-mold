'use client'

import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'

// Plan 10.4: personal account types (mail, train/forest booking, OAuth user tokens)
// are never shared or transferred; the backend refuses with personal_account_shared.
export function PersonalAccountBadge({ personal }: { personal: boolean | undefined }) {
  const t = useTranslations('credentials.personalAccount')
  if (!personal) return null
  return (
    <Badge variant="secondary" title={t('hint')}>
      <span>{t('badge')}</span>
      <span className="sr-only">{t('hint')}</span>
    </Badge>
  )
}
