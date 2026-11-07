'use client'

import { useTranslations } from 'next-intl'
import { ActivityIcon } from 'lucide-react'

import { ResourceListState } from '@/components/shared/resource-list-state'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { useMemoryActivity } from '@/lib/hooks/use-memory'
import type { MemoryActivity } from '@/lib/types'
import { formatDisplayDateTime } from '@/lib/utils/display-format'

/** Numeric metadata the backend records per action (ids and sizes only, never the text). */
const COUNT_KEYS = [
  'deleted',
  'records',
  'proposals',
  'revisions_removed',
  'proposals_cleared',
  'proposed',
  'applied',
  'dropped',
]
const SCOPES = new Set(['all', 'user', 'agent'])
const OUTCOMES = new Set(['ok', 'failed', 'stopped', 'skipped'])
const POLICY_KEYS = new Set([
  'default_mode',
  'allowed_modes',
  'consolidation_enabled',
  'channel_personal_memory',
  'contacts_allowed',
])

export function MemoryActivityTab() {
  const t = useTranslations('appSettings.memory.activity')
  const activity = useMemoryActivity()
  const rows = activity.data ?? []

  return (
    <SettingsSectionCard
      title={
        <span className="flex items-center gap-2">
          <ActivityIcon className="size-4" aria-hidden />
          {t('title')}
        </span>
      }
      description={t('description')}
    >
      {!activity.isLoading && rows.length > 0 ? (
        <ul className="divide-y divide-border/70" data-testid="memory-activity-list">
          {rows.map((row) => (
            <ActivityRow key={row.id} row={row} />
          ))}
        </ul>
      ) : (
        <ResourceListState
          loading={activity.isLoading}
          error={activity.isError}
          onRetry={() => void activity.refetch()}
          retryLabel={t('retry')}
          skeleton={<p className="text-sm text-muted-foreground">{t('loading')}</p>}
          emptyTitle={t('emptyTitle')}
          emptyDescription={t('emptyDescription')}
          filteredEmptyTitle={t('emptyTitle')}
        />
      )}
    </SettingsSectionCard>
  )
}

function ActivityRow({ row }: { row: MemoryActivity }) {
  const t = useTranslations('appSettings.memory.activity')
  const tScope = useTranslations('appSettings.memory.scopeSelect')
  const tPolicy = useTranslations('appSettings.memory.tenantPolicy')
  const actionKey = row.action.replace(/^memory\./, '')
  const scope = row.metadata.scope
  const counts = COUNT_KEYS.flatMap((key) => {
    const value = row.metadata[key]
    return typeof value === 'number' ? [t(`counts.${key}`, { count: value })] : []
  })
  // memory.consolidation_run records an outcome; memory.tenant_policy_update the changed keys.
  const outcome = row.metadata.outcome
  const changed = Array.isArray(row.metadata.changed)
    ? row.metadata.changed.filter((key): key is string => typeof key === 'string')
    : []

  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-3">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">
          {t.has(`action.${actionKey}`) ? t(`action.${actionKey}`) : t('action.unknown')}
        </span>
        {typeof scope === 'string' && SCOPES.has(scope) ? (
          <Badge variant="outline">{tScope(scope as 'all' | 'user' | 'agent')}</Badge>
        ) : null}
        {typeof outcome === 'string' ? (
          <Badge variant={outcome === 'failed' ? 'destructive' : 'outline'}>
            {OUTCOMES.has(outcome) ? t(`outcome.${outcome}`) : outcome}
          </Badge>
        ) : null}
        {changed.length > 0 ? (
          <span className="text-xs text-muted-foreground">
            {t('changed', {
              keys: changed
                .map((key) => (POLICY_KEYS.has(key) ? tPolicy(`fields.${key}.label`) : key))
                .join(', '),
            })}
          </span>
        ) : null}
        {counts.length > 0 ? (
          <span className="text-xs text-muted-foreground">{counts.join(', ')}</span>
        ) : null}
      </div>
      <time dateTime={row.created_at} className="text-xs text-muted-foreground">
        {formatDisplayDateTime(row.created_at)}
      </time>
    </li>
  )
}
