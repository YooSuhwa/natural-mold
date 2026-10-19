'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { MoreHorizontalIcon } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  usePlatformTenantTransition,
  useStartPlatformTenantExport,
  type PlatformTenantAction,
} from '@/lib/hooks/use-platform-tenants'
import type { PlatformTenant } from '@/lib/types/platform-tenant'
import { useTenantErrorMessage } from '../lib/messages'
import { TenantRiskyDialog, type TenantRiskyAction } from './tenant-risky-dialog'

/** Undoable or harmless actions (resume, cancel a deletion, start an export) with toasts. */
export function useTenantQuickActions() {
  const t = useTranslations('appSettings.platformTenants.actions')
  const errorMessage = useTenantErrorMessage()
  const transition = usePlatformTenantTransition()
  const exporter = useStartPlatformTenantExport()
  const onError = (err: unknown) => toast.error(errorMessage(err))
  return {
    pending: transition.isPending || exporter.isPending,
    run: (tenant: PlatformTenant, action: Exclude<PlatformTenantAction, 'suspend'>) =>
      transition.mutate(
        { tenant, action },
        { onSuccess: () => toast.success(t(`${action}Done`, { name: tenant.name })), onError },
      ),
    startExport: (tenant: PlatformTenant) =>
      exporter.mutate(tenant.id, {
        onSuccess: () => toast.success(t('exportStarted', { name: tenant.name })),
        onError,
      }),
  }
}

/**
 * S03 row menu: details, suspend/resume, export, schedule/cancel deletion. There is no
 * entry into the company's own content (agents, conversations).
 */
export function TenantActionsMenu({
  tenant,
  showDetail = true,
}: {
  tenant: PlatformTenant
  showDetail?: boolean
}) {
  const t = useTranslations('appSettings.platformTenants.actions')
  const quick = useTenantQuickActions()
  const [risky, setRisky] = useState<TenantRiskyAction | null>(null)
  const scheduled = tenant.status === 'deletion_scheduled'

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="ghost" size="icon-sm" aria-label={t('menu', { name: tenant.name })} />
          }
        >
          <MoreHorizontalIcon aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {showDetail ? (
            <DropdownMenuItem
              render={<Link href={`/settings/admin/tenants/${encodeURIComponent(tenant.id)}`} />}
            >
              {t('detail')}
            </DropdownMenuItem>
          ) : null}
          {tenant.status === 'active' ? (
            <DropdownMenuItem onClick={() => setRisky('suspend')}>{t('suspend')}</DropdownMenuItem>
          ) : null}
          {tenant.status === 'suspended' ? (
            <DropdownMenuItem disabled={quick.pending} onClick={() => quick.run(tenant, 'resume')}>
              {t('resume')}
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem disabled={quick.pending} onClick={() => quick.startExport(tenant)}>
            {t('export')}
          </DropdownMenuItem>
          {scheduled ? (
            <DropdownMenuItem
              disabled={quick.pending}
              onClick={() => quick.run(tenant, 'cancelDeletion')}
            >
              {t('cancelDeletion')}
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem variant="destructive" onClick={() => setRisky('scheduleDeletion')}>
              {t('scheduleDeletion')}
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      {risky ? (
        <TenantRiskyDialog tenant={tenant} action={risky} onClose={() => setRisky(null)} />
      ) : null}
    </>
  )
}
