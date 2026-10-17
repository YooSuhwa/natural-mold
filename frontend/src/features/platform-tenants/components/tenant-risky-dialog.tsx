'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import {
  usePlatformTenantTransition,
  usePlatformTenantUsage,
  useSchedulePlatformTenantDeletion,
} from '@/lib/hooks/use-platform-tenants'
import type { PlatformTenant } from '@/lib/types/platform-tenant'
import { useTenantErrorMessage } from '../lib/messages'

export type TenantRiskyAction = 'suspend' | 'scheduleDeletion'

interface TenantRiskyDialogProps {
  tenant: PlatformTenant
  action: TenantRiskyAction | null
  onClose: () => void
}

/**
 * 13.12.2 "회사 정지, 삭제 예약 (S04)": shows the member and active run counts, and the
 * operator types the company slug. Deletion is permanent after the 30-day grace period
 * (the I-07 purge job); "재개" and "예약 취소" undo within it.
 */
export function TenantRiskyDialog({ tenant, action, onClose }: TenantRiskyDialogProps) {
  const t = useTranslations('appSettings.platformTenants.risky')
  const errorMessage = useTenantErrorMessage()
  const usage = usePlatformTenantUsage(tenant.id)
  const transition = usePlatformTenantTransition()
  const schedule = useSchedulePlatformTenantDeletion()
  const [error, setError] = useState<string | null>(null)
  const kind = action ?? 'suspend'
  const pending = transition.isPending || schedule.isPending
  const members = usage.data?.members ?? tenant.active_member_count
  const runs = usage.data?.active_runs
  const impact = [
    t('impact.members', { count: members }),
    runs === null || runs === undefined
      ? t('impact.runsUnknown')
      : t('impact.runs', { count: runs }),
    t(`${kind}.effect`),
  ].join('\n')

  function close(open: boolean) {
    if (open) return
    setError(null)
    onClose()
  }

  function done(message: string) {
    toast.success(message)
    close(false)
  }

  function fail(err: unknown) {
    const message = errorMessage(err)
    setError(message)
    toast.error(message)
  }

  function confirm(typed?: string) {
    setError(null)
    if (kind === 'suspend') {
      transition.mutate(
        { tenant, action: 'suspend' },
        { onSuccess: () => done(t('suspend.done', { name: tenant.name })), onError: fail },
      )
      return
    }
    schedule.mutate(
      { tenant, slug: typed ?? '' },
      { onSuccess: () => done(t('scheduleDeletion.done', { name: tenant.name })), onError: fail },
    )
  }

  return (
    <DeleteConfirmDialog
      open={action !== null}
      onOpenChange={close}
      title={t(`${kind}.title`, { name: tenant.name })}
      description={impact}
      onConfirm={confirm}
      isPending={pending}
      confirmLabel={t(`${kind}.confirm`)}
      confirmText={tenant.slug}
      confirmTextLabel={t('slugLabel', { slug: tenant.slug })}
      error={error}
    />
  )
}
