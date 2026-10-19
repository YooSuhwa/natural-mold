'use client'

import Link from 'next/link'
import { useFormatter, useTranslations } from 'next-intl'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { PageHeader } from '@/components/shared/page-header'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  usePlatformTenant,
  usePlatformTenantDeletion,
  usePlatformTenantUsage,
} from '@/lib/hooks/use-platform-tenants'
import type { PlatformTenant } from '@/lib/types/platform-tenant'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import { cn } from '@/lib/utils'
import { formatMoney, toAmount } from '../lib/messages'
import { TenantActionsMenu } from './tenant-actions-menu'
import { TenantDataSection } from './tenant-data-section'
import { TenantLimitsForm } from './tenant-limits-form'
import { TenantStatusChip } from './tenant-status-chip'

/**
 * S04 회사 상세 (plan 13.14): overview, limits, data (exports and deletion schedule).
 * No tab reaches the company's content (agents, conversations).
 */
export function TenantDetailContent({ tenantId }: { tenantId: string }) {
  const t = useTranslations('appSettings.platformTenants.detail')
  const query = usePlatformTenant(tenantId)
  const deletion = usePlatformTenantDeletion(tenantId)
  const back = (
    <Link
      href="/settings/admin/tenants"
      className={cn(buttonVariants({ size: 'sm', variant: 'outline' }))}
    >
      {t('back')}
    </Link>
  )
  if (query.isPending) {
    return <Skeleton className="h-48 w-full" data-testid="tenant-detail-loading" />
  }
  if (query.isError) {
    return (
      <ApiErrorState error={query.error} onRetry={() => void query.refetch()}>
        {back}
      </ApiErrorState>
    )
  }
  const tenant = query.data
  const dueAt = deletion.data?.deletion_due_at ?? null
  return (
    <div className="space-y-4">
      <PageHeader
        title={tenant.name}
        description={t('description', { slug: tenant.slug })}
        action={
          <>
            <TenantStatusChip status={tenant.status} dueAt={dueAt} />
            <TenantActionsMenu tenant={tenant} showDetail={false} />
            {back}
          </>
        }
      />
      <TenantOverview tenant={tenant} />
      {/* Keyed by version: a refetch after a change reseeds the form. */}
      <TenantLimitsForm key={tenant.updated_at} tenant={tenant} />
      <TenantDataSection tenant={tenant} dueAt={dueAt} />
    </div>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="moldy-muted-panel p-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-xl font-semibold tabular-nums text-foreground">{value}</dd>
      {hint ? <dd className="mt-1 text-xs text-muted-foreground">{hint}</dd> : null}
    </div>
  )
}

function TenantOverview({ tenant }: { tenant: PlatformTenant }) {
  const t = useTranslations('appSettings.platformTenants.detail')
  const tStatus = useTranslations('appSettings.platformTenants.status')
  const format = useFormatter()
  const usage = usePlatformTenantUsage(tenant.id)
  const noLimit = t('noLimit')
  const count = (value: number | null | undefined) =>
    value === null || value === undefined ? noLimit : format.number(value)
  const data = usage.data
  return (
    <SettingsSectionCard title={t('overview')} description={t('overviewHint')}>
      <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <Stat label={t('status')} value={tStatus(tenant.status)} />
        <Stat label={t('createdAt')} value={formatDisplayDateTime(tenant.created_at)} />
        <Stat label={t('organizations')} value={format.number(tenant.organization_count)} />
        <Stat
          label={t('members')}
          value={t('ofLimit', {
            used: format.number(data?.members ?? tenant.active_member_count),
            limit: count(tenant.limits.member_limit),
          })}
        />
        {usage.isError ? (
          <Stat label={t('spend')} value={t('usageUnavailable')} />
        ) : (
          <Stat
            label={t('spend')}
            value={
              data
                ? t('ofLimit', {
                    used: formatMoney(toAmount(data.used), noLimit),
                    limit: formatMoney(toAmount(data.monthly_spend_limit), noLimit),
                  })
                : t('usageLoading')
            }
            hint={data ? t('resetsAt', { date: formatDisplayDateTime(data.resets_at) }) : undefined}
          />
        )}
        <Stat
          label={t('activeRuns')}
          value={
            data
              ? t('ofLimit', {
                  used: count(data.active_runs ?? 0),
                  limit: count(tenant.limits.concurrent_run_limit),
                })
              : usage.isError
                ? t('usageUnavailable')
                : t('usageLoading')
          }
        />
      </dl>
    </SettingsSectionCard>
  )
}
