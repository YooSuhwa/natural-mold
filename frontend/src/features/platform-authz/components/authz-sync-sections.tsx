'use client'

import { useFormatter, useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { useOutboxFailures, useRetryOutboxFailure } from '@/lib/hooks/use-admin-authz'
import type { AuthzOpsStatus, OutboxFailure } from '@/lib/types/admin-authz'
import { DATE_TIME, useAuthzErrorMessage } from '../lib/messages'

const KNOWN_ERROR_TYPES = new Set(['AuthzUnavailable', 'TimeoutError'])
const KNOWN_JOBS = new Set([
  'authz_outbox_drain',
  'authz_reconcile',
  'authz_expiry_cleanup',
  'authz_backlog_alert',
  'authz_delegation_validate',
  'retention_sweep',
])

function shortId(id: string): string {
  return id.slice(0, 8)
}

function FailureRow({ item, syncEnabled }: { item: OutboxFailure; syncEnabled: boolean }) {
  const t = useTranslations('appSettings.authzOps.failures')
  const retry = useRetryOutboxFailure()
  const errorMessage = useAuthzErrorMessage()
  const resource = `${item.resource_type} ${shortId(item.resource_id)}`
  const errorKey =
    item.error_type && KNOWN_ERROR_TYPES.has(item.error_type) ? item.error_type : 'other'
  const op = item.op === 'delete' ? 'delete' : 'write'

  async function onRetry() {
    try {
      await retry.mutateAsync(item.id)
      toast.success(t('retried'))
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }

  return (
    <TableRow>
      <TableCell className="font-mono text-xs">
        {item.tenant_id ? shortId(item.tenant_id) : t('platform')}
      </TableCell>
      <TableCell className="font-mono text-xs" title={item.resource_id}>
        {resource}
      </TableCell>
      <TableCell>{t(`ops.${op}`)}</TableCell>
      <TableCell className="tabular-nums">{item.attempts}</TableCell>
      <TableCell>{t(`errorTypes.${errorKey}`)}</TableCell>
      <TableCell className="text-right">
        <Button
          size="sm"
          variant="outline"
          onClick={onRetry}
          disabled={!syncEnabled || retry.isPending}
          aria-label={t('retryLabel', { resource })}
        >
          {t('retry')}
        </Button>
      </TableCell>
    </TableRow>
  )
}

export function FailuresSection({ syncEnabled }: { syncEnabled: boolean }) {
  const t = useTranslations('appSettings.authzOps.failures')
  const { data } = useOutboxFailures()
  const items = data?.items ?? []
  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('company')}</TableHead>
              <TableHead>{t('resource')}</TableHead>
              <TableHead>{t('op')}</TableHead>
              <TableHead>{t('attempts')}</TableHead>
              <TableHead>{t('error')}</TableHead>
              <TableHead className="text-right">{t('action')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <FailureRow key={item.id} item={item} syncEnabled={syncEnabled} />
            ))}
          </TableBody>
        </Table>
      )}
    </SettingsSectionCard>
  )
}

export function ShadowSection({ rows }: { rows: AuthzOpsStatus['shadow'] }) {
  const t = useTranslations('appSettings.authzOps.shadow')
  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('type')}</TableHead>
              <TableHead className="text-right">{t('legacyOnly')}</TableHead>
              <TableHead className="text-right">{t('fgaOnly')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.type}>
                <TableCell className="font-mono text-xs">{row.type}</TableCell>
                <TableCell className="text-right tabular-nums">{row.legacy_only}</TableCell>
                <TableCell className="text-right tabular-nums">{row.fga_only}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </SettingsSectionCard>
  )
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums text-foreground">{value}</dd>
    </div>
  )
}

export function DelegationRetentionCards({ status }: { status: AuthzOpsStatus }) {
  const t = useTranslations('appSettings.authzOps')
  const format = useFormatter()
  const { delegations, retention } = status
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <SettingsSectionCard title={t('delegations.title')}>
        <dl>
          <Pair label={t('delegations.brokenLinks')} value={String(delegations.broken_links)} />
          <Pair label={t('delegations.brokenRecent')} value={String(delegations.broken_last_24h)} />
        </dl>
      </SettingsSectionCard>
      <SettingsSectionCard title={t('retention.title')}>
        <dl>
          <Pair
            label={t('retention.state')}
            value={retention.enabled ? t('retention.enabled') : t('retention.disabled')}
          />
          <Pair label={t('retention.schedule')} value={retention.schedule} />
          <Pair
            label={t('retention.lastRun')}
            value={
              retention.last_run_at
                ? format.dateTime(new Date(retention.last_run_at), DATE_TIME)
                : t('retention.never')
            }
          />
          <Pair label={t('retention.purgesRecent')} value={String(retention.purges_last_24h)} />
        </dl>
      </SettingsSectionCard>
    </div>
  )
}

export function JobsSection({ jobs }: { jobs: AuthzOpsStatus['jobs'] }) {
  const t = useTranslations('appSettings.authzOps.jobs')
  return (
    <SettingsSectionCard title={t('title')}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t('name')}</TableHead>
            <TableHead>{t('state')}</TableHead>
            <TableHead>{t('schedule')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {jobs.map((job) => (
            <TableRow key={job.id}>
              <TableCell>{KNOWN_JOBS.has(job.id) ? t(`names.${job.id}`) : job.id}</TableCell>
              <TableCell>
                <Badge variant={job.scheduled ? 'default' : 'secondary'}>
                  {job.scheduled ? t('scheduled') : t('notScheduled')}
                </Badge>
              </TableCell>
              <TableCell className="font-mono text-xs">{job.schedule ?? '-'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </SettingsSectionCard>
  )
}
