'use client'

import { useMemo, useState } from 'react'
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ClockIcon,
  RefreshCwIcon,
  SearchIcon,
  ShieldIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { ErrorState } from '@/components/shared/error-state'
import { cn } from '@/lib/utils'
import { useAuditEvents } from '@/lib/hooks/use-audit-events'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import type { AuditEvent, AuditScope } from '@/lib/types/audit'

const ALL_VALUE = '__all__'

const TARGET_TYPES = [
  'agent',
  'credential',
  'tool',
  'mcp_server',
  'skill',
  'trigger',
  'conversation',
  'agent_api_run',
  'marketplace_item',
  'model',
  'system_llm_setting',
] as const

const OUTCOMES = ['success', 'failure', 'denied', 'skipped'] as const

interface AuditEventsContentProps {
  scope: AuditScope
  admin?: boolean
}

interface FilterState {
  action: string
  targetType: string
  outcome: string
  requestId: string
  runId: string
}

const EMPTY_FILTERS: FilterState = {
  action: '',
  targetType: ALL_VALUE,
  outcome: ALL_VALUE,
  requestId: '',
  runId: '',
}

function clean(value: string) {
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

function selectValue(value: string) {
  return value === ALL_VALUE ? null : value
}

function formatDate(value: string) {
  return formatDisplayDateTime(value, {
    format: {
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    },
  })
}

function actorLabel(event: AuditEvent) {
  if (event.actor_email_snapshot) return event.actor_email_snapshot
  if (event.actor_label) return event.actor_label
  if (event.actor_type === 'api_key') return 'API key'
  if (event.actor_type === 'system') return 'System'
  return event.actor_type
}

function targetLabel(event: AuditEvent) {
  return event.target_name_snapshot || event.target_id || event.target_type
}

function outcomeClasses(outcome: string) {
  if (outcome === 'success') return 'moldy-status-surface moldy-status-success'
  if (outcome === 'failure') return 'moldy-status-surface moldy-status-danger'
  if (outcome === 'denied') return 'moldy-status-surface moldy-status-warn'
  return 'bg-muted text-muted-foreground ring-border'
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const t = useTranslations('appSettings.audit.outcomes')
  const Icon = outcome === 'success' ? CheckCircle2Icon : AlertTriangleIcon
  const label =
    outcome === 'success'
      ? t('success')
      : outcome === 'failure'
        ? t('failure')
        : outcome === 'denied'
          ? t('denied')
          : outcome === 'skipped'
            ? t('skipped')
            : outcome
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center gap-1 rounded-full px-2 text-xs font-medium ring-1 ring-inset',
        outcomeClasses(outcome),
      )}
    >
      <Icon className="size-3" aria-hidden />
      {label}
    </span>
  )
}

export function AuditEventsContent({ scope, admin = false }: AuditEventsContentProps) {
  const t = useTranslations('appSettings.audit')
  const [draft, setDraft] = useState<FilterState>(EMPTY_FILTERS)
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const query = useAuditEvents({
    scope,
    limit: 50,
    action: clean(filters.action),
    target_type: selectValue(filters.targetType),
    outcome: selectValue(filters.outcome),
    request_id: clean(filters.requestId),
    run_id: clean(filters.runId),
  })

  const events = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data])
  const selected = events.find((event) => event.id === selectedId) ?? events[0] ?? null
  const successCount = events.filter((event) => event.outcome === 'success').length
  const failureCount = events.filter((event) => event.outcome === 'failure').length
  const deniedCount = events.filter((event) => event.outcome === 'denied').length

  function applyFilters() {
    setFilters(draft)
    setSelectedId(null)
  }

  function resetFilters() {
    setDraft(EMPTY_FILTERS)
    setFilters(EMPTY_FILTERS)
    setSelectedId(null)
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold text-foreground">
            {admin ? t('adminTitle') : t('title')}
          </h2>
          <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
            {admin ? t('adminDescription') : t('description')}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => query.refetch()}>
          <RefreshCwIcon className="size-4" />
          {t('refresh')}
        </Button>
      </section>

      <section className="grid gap-3 md:grid-cols-4">
        <Metric label={t('metrics.loaded')} value={String(events.length)} />
        <Metric label={t('metrics.success')} value={String(successCount)} tone="success" />
        <Metric label={t('metrics.failure')} value={String(failureCount)} tone="danger" />
        <Metric label={t('metrics.denied')} value={String(deniedCount)} tone="warn" />
      </section>

      <section className="moldy-panel space-y-3 p-4">
        <div className="flex items-center gap-2">
          <SearchIcon className="size-4 text-muted-foreground" aria-hidden />
          <h3 className="text-sm font-semibold text-foreground">{t('filters.title')}</h3>
        </div>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_180px_150px]">
          <Input
            value={draft.action}
            onChange={(event) => setDraft((prev) => ({ ...prev, action: event.target.value }))}
            placeholder={t('filters.actionPlaceholder')}
            aria-label={t('filters.action')}
          />
          <Select
            value={draft.targetType}
            onValueChange={(value) =>
              setDraft((prev) => ({ ...prev, targetType: value ?? ALL_VALUE }))
            }
          >
            <SelectTrigger className="w-full">
              <span className="truncate">
                {draft.targetType === ALL_VALUE ? t('filters.allTargets') : draft.targetType}
              </span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_VALUE}>{t('filters.allTargets')}</SelectItem>
              {TARGET_TYPES.map((target) => (
                <SelectItem key={target} value={target}>
                  {target}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={draft.outcome}
            onValueChange={(value) =>
              setDraft((prev) => ({ ...prev, outcome: value ?? ALL_VALUE }))
            }
          >
            <SelectTrigger className="w-full">
              <span className="truncate">
                {draft.outcome === ALL_VALUE
                  ? t('filters.allOutcomes')
                  : t(`outcomes.${draft.outcome}`)}
              </span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_VALUE}>{t('filters.allOutcomes')}</SelectItem>
              {OUTCOMES.map((outcome) => (
                <SelectItem key={outcome} value={outcome}>
                  {t(`outcomes.${outcome}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <Input
            value={draft.requestId}
            onChange={(event) => setDraft((prev) => ({ ...prev, requestId: event.target.value }))}
            placeholder={t('filters.requestPlaceholder')}
            aria-label={t('filters.requestId')}
          />
          <Input
            value={draft.runId}
            onChange={(event) => setDraft((prev) => ({ ...prev, runId: event.target.value }))}
            placeholder={t('filters.runPlaceholder')}
            aria-label={t('filters.runId')}
          />
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            {t('filters.reset')}
          </Button>
          <Button size="sm" onClick={applyFilters}>
            {t('filters.apply')}
          </Button>
        </div>
      </section>

      {query.isError ? (
        <ErrorState onRetry={() => query.refetch()} />
      ) : (
        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="moldy-panel overflow-hidden">
            <div className="border-b border-border/70 px-4 py-3">
              <h3 className="text-sm font-semibold text-foreground">{t('events')}</h3>
            </div>
            {query.isLoading ? (
              <div className="space-y-3 p-4">
                {Array.from({ length: 5 }).map((_, index) => (
                  <div key={index} className="h-16 animate-pulse rounded-lg bg-muted" />
                ))}
              </div>
            ) : events.length === 0 ? (
              <div className="p-6 text-sm text-muted-foreground">{t('empty')}</div>
            ) : (
              <div className="divide-y divide-border/70">
                {events.map((event) => (
                  <button
                    key={event.id}
                    type="button"
                    onClick={() => setSelectedId(event.id)}
                    className={cn(
                      'grid w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/60 md:grid-cols-[170px_minmax(0,1fr)_160px]',
                      selected?.id === event.id && 'bg-muted',
                    )}
                  >
                    <div className="space-y-1">
                      <p className="flex items-center gap-1 text-xs text-muted-foreground">
                        <ClockIcon className="size-3" aria-hidden />
                        {formatDate(event.created_at)}
                      </p>
                      <OutcomeBadge outcome={event.outcome} />
                    </div>
                    <div className="min-w-0 space-y-1">
                      <p className="truncate font-mono text-sm font-semibold text-foreground">
                        {event.action}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {event.target_type} · {targetLabel(event)}
                      </p>
                      {event.reason_code ? (
                        <p className="truncate text-xs text-status-danger">{event.reason_code}</p>
                      ) : null}
                    </div>
                    <div className="min-w-0 space-y-1">
                      <p className="truncate text-xs font-medium text-foreground">
                        {actorLabel(event)}
                      </p>
                      <p className="truncate font-mono text-xs text-muted-foreground">
                        {event.request_id ?? event.run_id ?? event.id}
                      </p>
                    </div>
                  </button>
                ))}
              </div>
            )}
            {query.hasNextPage ? (
              <div className="border-t border-border/70 p-3">
                <Button
                  variant="outline"
                  className="w-full"
                  onClick={() => query.fetchNextPage()}
                  disabled={query.isFetchingNextPage}
                >
                  <ChevronDownIcon className="size-4" />
                  {query.isFetchingNextPage ? t('loadingMore') : t('loadMore')}
                </Button>
              </div>
            ) : null}
          </div>

          <AuditDetail event={selected} />
        </section>
      )}
    </div>
  )
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'success' | 'danger' | 'warn'
}) {
  const toneClass =
    tone === 'success'
      ? 'text-status-success'
      : tone === 'danger'
        ? 'text-status-danger'
        : tone === 'warn'
          ? 'text-status-warn'
          : 'text-foreground'
  return (
    <div className="moldy-card space-y-1 p-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className={cn('text-xl font-semibold', toneClass)}>{value}</p>
    </div>
  )
}

function AuditDetail({ event }: { event: AuditEvent | null }) {
  const t = useTranslations('appSettings.audit.detail')
  if (!event) {
    return <aside className="moldy-panel p-4 text-sm text-muted-foreground">{t('empty')}</aside>
  }

  const metadata = event.metadata ? JSON.stringify(event.metadata, null, 2) : t('none')

  return (
    <aside className="moldy-panel h-fit space-y-4 p-4">
      <div className="flex items-start gap-2">
        <ShieldIcon className="mt-0.5 size-4 text-muted-foreground" aria-hidden />
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-foreground">{event.action}</h3>
          <p className="text-xs text-muted-foreground">{formatDate(event.created_at)}</p>
        </div>
      </div>

      <dl className="grid gap-2 text-xs">
        <DetailRow label={t('outcome')} value={event.outcome} />
        <DetailRow label={t('actor')} value={actorLabel(event)} />
        <DetailRow label={t('target')} value={`${event.target_type} · ${targetLabel(event)}`} />
        <DetailRow label={t('requestId')} value={event.request_id} mono />
        <DetailRow label={t('runId')} value={event.run_id} mono />
        <DetailRow label={t('traceId')} value={event.trace_id} mono />
        <DetailRow label={t('reason')} value={event.reason_message ?? event.reason_code} />
        <DetailRow label={t('ip')} value={event.ip_address} mono />
      </dl>

      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">{t('metadata')}</p>
        <pre className="max-h-80 overflow-auto rounded-lg bg-muted p-3 font-mono text-xs leading-5 text-foreground">
          {metadata}
        </pre>
      </div>
    </aside>
  )
}

function DetailRow({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string | null
  mono?: boolean
}) {
  return (
    <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn('min-w-0 truncate text-foreground', mono && 'font-mono')}>
        {value || '-'}
      </dd>
    </div>
  )
}
