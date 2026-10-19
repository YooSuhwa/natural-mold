'use client'

import { useTranslations } from 'next-intl'

import { StatusChip } from '@/components/shared/status-chip'
import type { PlatformTenantStatus } from '@/lib/types/platform-tenant'
import { daysUntil } from '../lib/messages'

const VARIANT: Record<PlatformTenantStatus, string> = {
  active: 'active',
  suspended: 'suspended',
  deletion_scheduled: 'blocked',
}

/** Status with text, never color alone (13.12.3); D-n while a deletion is scheduled. */
export function TenantStatusChip({
  status,
  dueAt,
}: {
  status: PlatformTenantStatus
  dueAt?: string | null
}) {
  const t = useTranslations('appSettings.platformTenants.status')
  const label =
    status === 'deletion_scheduled' && dueAt
      ? t('deletionScheduledDue', { days: daysUntil(dueAt) })
      : t(status)
  return <StatusChip variant={VARIANT[status]} label={label} />
}
