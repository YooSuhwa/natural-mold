'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { CircleAlert, CircleCheck, CircleX, RefreshCw } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { useSession } from '@/lib/auth/session'
import { useSystemStatus } from '@/lib/hooks/use-system-status'
import type { SystemStatusOut, SystemVerdict } from '@/lib/types/system-status'
import { cn } from '@/lib/utils'
import { formatDisplayDateTime, formatDisplayNumber } from '@/lib/utils/display-format'

const VERDICT_CLASS: Record<SystemVerdict, string> = {
  ok: 'moldy-status-success',
  warning: 'moldy-status-warn',
  error: 'moldy-status-danger',
}

const VERDICT_ICON = { ok: CircleCheck, warning: CircleAlert, error: CircleX } as const

/**
 * 시스템 구성 (plan §11, D49): this installation's components against its install profile,
 * the job workers and the queue. Platform operators only; the backend enforces it, this guard
 * only avoids 403 noise for a bookmarked URL.
 */
export function SystemStatusContent() {
  const t = useTranslations('systemStatus')
  const router = useRouter()
  const { data: user, isPending } = useSession()
  const denied = !isPending && !!user && !user.is_super_user

  useEffect(() => {
    if (denied) router.replace('/')
  }, [denied, router])

  if (isPending || denied) {
    return <p className="text-sm text-muted-foreground">{t('loading')}</p>
  }
  return <SystemStatus />
}

function SystemStatus() {
  const t = useTranslations('systemStatus')
  const { data, isLoading, isError, refetch, isFetching } = useSystemStatus()

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t('title')}
        description={t('description')}
        action={
          <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
            <RefreshCw className={cn('size-3.5', isFetching && 'animate-spin')} />
            {t('refresh')}
          </Button>
        }
      />
      {isLoading ? (
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      ) : isError || !data ? (
        <p className="moldy-status-danger moldy-status-text text-sm">{t('loadError')}</p>
      ) : (
        <StatusBody status={data} />
      )}
    </div>
  )
}

function VerdictBadge({ verdict }: { verdict: SystemVerdict }) {
  const t = useTranslations('systemStatus')
  const Icon = VERDICT_ICON[verdict]
  return (
    <span
      className={cn(
        VERDICT_CLASS[verdict],
        'moldy-status-soft inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium',
      )}
      data-verdict={verdict}
    >
      <Icon className="size-3.5" />
      {t(`verdict.${verdict}`)}
    </span>
  )
}

function StatusBody({ status }: { status: SystemStatusOut }) {
  const t = useTranslations('systemStatus')
  return (
    <>
      <div
        className={cn(VERDICT_CLASS[status.verdict], 'moldy-status-surface moldy-status-card')}
        data-testid="system-status-summary"
      >
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
          {t(`summary.${status.verdict}`)}
          <Badge variant="outline">{t(`profile.${status.profile}.name`)}</Badge>
        </p>
        <p className="mt-1 text-xs">{t(`profile.${status.profile}.description`)}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {t('checkedAt', { time: formatDisplayDateTime(status.checked_at) })} · {t('profileHint')}
        </p>
      </div>

      <SettingsSectionCard title={t('components.title')} description={t('components.description')}>
        <ul className="mt-4 divide-y divide-border">
          {status.components.map((component) => (
            <li
              key={component.key}
              className="flex flex-col gap-1 py-3 sm:flex-row sm:items-start sm:justify-between"
              data-component={component.key}
            >
              <div className="min-w-0 space-y-0.5">
                <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
                  {t(`component.${component.key}`)}
                  <Badge variant="outline">{t(`requirement.${component.requirement}`)}</Badge>
                </p>
                <p className="text-xs text-muted-foreground">
                  {t(`found.${component.found}`)} ·{' '}
                  <span className="font-mono">{component.detail}</span>
                </p>
              </div>
              <VerdictBadge verdict={component.verdict} />
            </li>
          ))}
        </ul>
      </SettingsSectionCard>

      <SettingsSectionCard title={t('jobs.title')} description={t('jobs.description')}>
        <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {(['queued', 'running', 'overdue', 'failed_last_day'] as const).map((key) => (
            <div key={key} className="moldy-muted-panel p-3">
              <dt className="text-xs text-muted-foreground">{t(`jobs.${key}`)}</dt>
              <dd className="text-lg font-semibold text-foreground" data-queue={key}>
                {formatDisplayNumber(status.queue[key])}
              </dd>
            </div>
          ))}
        </dl>
        {status.workers.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">{t('jobs.noWorkers')}</p>
        ) : (
          <ul className="mt-4 divide-y divide-border">
            {status.workers.map((worker) => (
              <li
                key={worker.worker_id}
                className="flex flex-col gap-1 py-2 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="truncate font-mono text-xs text-foreground">{worker.worker_id}</p>
                  <p className="text-xs text-muted-foreground">
                    {t('jobs.heartbeat', { time: formatDisplayDateTime(worker.heartbeat_at) })}
                  </p>
                </div>
                <VerdictBadge verdict={worker.live ? 'ok' : 'warning'} />
              </li>
            ))}
          </ul>
        )}
      </SettingsSectionCard>
    </>
  )
}
