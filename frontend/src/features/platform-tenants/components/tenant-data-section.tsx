'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  downloadPlatformTenantExport,
  usePlatformTenantExports,
} from '@/lib/hooks/use-platform-tenants'
import type { PlatformTenant, PlatformTenantExport } from '@/lib/types/platform-tenant'
import { formatDisplayBytes, formatDisplayDateTime } from '@/lib/utils/display-format'
import { daysUntil } from '../lib/messages'
import { useTenantQuickActions } from './tenant-actions-menu'
import { TenantRiskyDialog } from './tenant-risky-dialog'

/** S04 데이터: export history (one at a time) and the deletion schedule. */
export function TenantDataSection({
  tenant,
  dueAt,
}: {
  tenant: PlatformTenant
  dueAt: string | null
}) {
  const t = useTranslations('appSettings.platformTenants.data')
  const quick = useTenantQuickActions()
  const exports = usePlatformTenantExports(tenant.id)
  const [scheduling, setScheduling] = useState(false)
  const items = exports.data?.items ?? []
  const live = items.some((job) => job.status === 'queued' || job.status === 'running')
  const scheduled = tenant.status === 'deletion_scheduled'

  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      <div className="space-y-6">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-medium text-foreground">{t('exports')}</h3>
            <Button
              size="sm"
              variant="outline"
              disabled={live || quick.pending}
              onClick={() => quick.startExport(tenant)}
            >
              {t('startExport')}
            </Button>
          </div>
          {exports.isError ? (
            <ApiErrorState error={exports.error} onRetry={() => void exports.refetch()} />
          ) : items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {exports.isPending ? t('exportsLoading') : t('noExports')}
            </p>
          ) : (
            <ExportTable tenantId={tenant.id} items={items} />
          )}
        </div>
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-foreground">{t('deletion')}</h3>
          {scheduled ? (
            <>
              <p className="text-sm text-status-danger">
                {dueAt
                  ? t('scheduledUntil', {
                      date: formatDisplayDateTime(dueAt),
                      days: daysUntil(dueAt),
                    })
                  : t('scheduled')}
              </p>
              <p className="text-xs text-muted-foreground">{t('scheduledHint')}</p>
              <Button
                variant="outline"
                disabled={quick.pending}
                onClick={() => quick.run(tenant, 'cancelDeletion')}
              >
                {t('cancelDeletion')}
              </Button>
            </>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">{t('deletionHint')}</p>
              <Button variant="destructive" onClick={() => setScheduling(true)}>
                {t('scheduleDeletion')}
              </Button>
            </>
          )}
        </div>
      </div>
      {scheduling ? (
        <TenantRiskyDialog
          tenant={tenant}
          action="scheduleDeletion"
          onClose={() => setScheduling(false)}
        />
      ) : null}
    </SettingsSectionCard>
  )
}

function ExportTable({ tenantId, items }: { tenantId: string; items: PlatformTenantExport[] }) {
  const t = useTranslations('appSettings.platformTenants.data')
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t('createdAt')}</TableHead>
          <TableHead scope="col">{t('status')}</TableHead>
          <TableHead scope="col">{t('expiresAt')}</TableHead>
          <TableHead scope="col">
            <span className="sr-only">{t('download')}</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((job) => (
          <TableRow key={job.id}>
            <TableCell>{formatDisplayDateTime(job.created_at)}</TableCell>
            <TableCell>
              {job.status === 'running'
                ? t('jobRunning', { progress: job.progress })
                : job.status === 'failed'
                  ? t('jobFailed', { code: job.error_code ?? '-' })
                  : t(`job.${job.status}`)}
              {job.size_bytes !== null ? (
                <span className="block text-xs text-muted-foreground">
                  {formatDisplayBytes(job.size_bytes)}
                </span>
              ) : null}
            </TableCell>
            <TableCell>{job.expires_at ? formatDisplayDateTime(job.expires_at) : '-'}</TableCell>
            <TableCell className="text-right">
              {job.download_available ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    void downloadPlatformTenantExport(tenantId, job.id).catch(() =>
                      toast.error(t('downloadFailed')),
                    )
                  }}
                >
                  {t('download')}
                </Button>
              ) : null}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
