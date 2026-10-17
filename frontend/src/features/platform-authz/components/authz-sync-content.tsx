'use client'

import { useState } from 'react'
import { useFormatter, useTranslations } from 'next-intl'
import { AlertTriangleIcon, RefreshCwIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { useAuthzOpsStatus, useStartReconcile } from '@/lib/hooks/use-admin-authz'
import type { AuthzOpsStatus } from '@/lib/types/admin-authz'
import {
  DelegationRetentionCards,
  FailuresSection,
  JobsSection,
  ShadowSection,
} from './authz-sync-sections'
import { DATE_TIME, useAuthzErrorMessage } from '../lib/messages'

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="moldy-card p-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tabular-nums text-foreground">{value}</dd>
      {hint ? <dd className="mt-1 text-xs text-muted-foreground">{hint}</dd> : null}
    </div>
  )
}

function ConnectionBanner({ status }: { status: AuthzOpsStatus }) {
  const t = useTranslations('appSettings.authzOps.banner')
  const format = useFormatter()
  const { openfga } = status
  if (openfga.status === 'disabled') {
    return <p className="moldy-card p-4 text-sm text-muted-foreground">{t('legacy')}</p>
  }
  if (openfga.status === 'degraded') {
    return (
      <p role="status" className="moldy-card p-4 text-sm text-muted-foreground">
        {t('degraded', { latency: openfga.latency_ms ?? 0 })}
      </p>
    )
  }
  if (openfga.status !== 'down') return null
  const last = openfga.last_success_at
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4"
    >
      <AlertTriangleIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-destructive" />
      <div className="space-y-1">
        <p className="text-sm font-medium text-destructive">{t('downTitle')}</p>
        <p className="text-xs text-muted-foreground">
          {last
            ? t('lastSuccess', { time: format.dateTime(new Date(last), DATE_TIME) })
            : t('neverSucceeded')}
        </p>
      </div>
    </div>
  )
}

function Stats({ status }: { status: AuthzOpsStatus }) {
  const t = useTranslations('appSettings.authzOps.stats')
  const format = useFormatter()
  const last = status.reconcile.last
  const lastWhen = last?.finished_at ?? last?.created_at
  return (
    <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Stat
        label={t('outboxPending')}
        value={String(status.outbox.pending)}
        hint={t('outboxOldest', { seconds: Math.round(status.outbox.lag_seconds) })}
      />
      <Stat
        label={t('failed')}
        value={String(status.outbox.failed)}
        hint={t('failedHint', { retrying: status.outbox.retrying })}
      />
      <Stat
        label={t('lastReconcile')}
        value={lastWhen ? format.dateTime(new Date(lastWhen), DATE_TIME) : t('noReconcile')}
        hint={
          last?.report
            ? t('lastReconcileHint', {
                diff: last.report.diff,
                repaired: last.report.repaired,
                deferred: last.report.deferred,
              })
            : undefined
        }
      />
      <Stat
        label={t('revocations')}
        value={String(status.revocations.pending)}
        hint={t('revocationsHint', { stuck: status.revocations.stuck })}
      />
    </dl>
  )
}

function ReconcileButton({ status }: { status: AuthzOpsStatus }) {
  const t = useTranslations('appSettings.authzOps.reconcile')
  const errorMessage = useAuthzErrorMessage()
  const start = useStartReconcile()
  const [open, setOpen] = useState(false)
  const active = status.reconcile.active
  const disabled = !status.sync_enabled || Boolean(active) || start.isPending

  async function confirm() {
    setOpen(false)
    try {
      await start.mutateAsync()
      toast.success(t('started'))
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button onClick={() => setOpen(true)} disabled={disabled}>
        <RefreshCwIcon aria-hidden className={active ? 'animate-spin' : undefined} />
        {active ? t('running') : t('button')}
      </Button>
      {!status.sync_enabled ? (
        <p className="max-w-xs text-right text-xs text-muted-foreground">{t('disabledHint')}</p>
      ) : null}
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('confirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription>{t('confirmDescription')}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
            <AlertDialogAction onClick={confirm}>{t('confirm')}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function ActiveRun({ status }: { status: AuthzOpsStatus }) {
  const t = useTranslations('appSettings.authzOps')
  const active = status.reconcile.active
  if (!active) return null
  const total = Math.max(active.partitions_total, 0)
  return (
    <div role="status" className="moldy-card space-y-2 p-4">
      <p className="text-sm font-medium text-foreground">
        {t('reconcile.running')} · {t(`runStatus.${active.status}`)}
      </p>
      {total > 0 ? (
        <>
          <progress className="h-2 w-full" value={active.partitions_done} max={total} />
          <p className="text-xs text-muted-foreground">
            {t('reconcile.progress', { done: active.partitions_done, total })}
          </p>
        </>
      ) : null}
    </div>
  )
}

/** S27 권한 동기화 상태 (plan 14.3; H-04): super user, both deployment modes. */
export function AuthzSyncContent() {
  const t = useTranslations('appSettings.authzOps')
  const { data: status, isLoading, isError } = useAuthzOpsStatus()

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t('title')}
        description={t('description')}
        action={status ? <ReconcileButton status={status} /> : null}
      />
      {isLoading ? <p className="text-sm text-muted-foreground">{t('loading')}</p> : null}
      {isError ? (
        <p role="alert" className="text-sm text-destructive">
          {t('loadError')}
        </p>
      ) : null}
      {status ? (
        <>
          <ConnectionBanner status={status} />
          <ActiveRun status={status} />
          <Stats status={status} />
          <FailuresSection syncEnabled={status.sync_enabled} />
          <ShadowSection rows={status.shadow} />
          <DelegationRetentionCards status={status} />
          <JobsSection jobs={status.jobs} />
        </>
      ) : null}
    </div>
  )
}
