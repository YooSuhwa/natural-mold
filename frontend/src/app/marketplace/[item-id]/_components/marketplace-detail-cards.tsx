'use client'

import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { EmptyState } from '@/components/shared/empty-state'
import { getDomainIconIdForResource } from '@/components/shared/icon'
import type { MarketplaceItem, MarketplaceVersionSummary } from '@/lib/types/marketplace'
import { formatMediumDate } from '@/lib/utils/format-relative-time'

function shortHash(value?: string | null): string | null {
  if (!value) return null
  return value.slice(0, 7)
}

function formatExecutionProfileValue(
  key: string,
  value: unknown,
  t: (key: string) => string,
): string {
  if (key === 'support_level' && typeof value === 'string') {
    return t(`executionProfile.supportLevel.${value}`)
  }
  if (key === 'requires_network' && typeof value === 'boolean') {
    return value
      ? t('executionProfile.boolean.required')
      : t('executionProfile.boolean.notRequired')
  }
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(', ') : t('executionProfile.none')
  }
  if (value == null || value === '') return '—'
  return String(value)
}

/** Versions and execution profile cards of the item detail (moved out of the page). */
export function MarketplaceDetailCards({
  item,
  versions,
}: {
  item: MarketplaceItem
  versions: MarketplaceVersionSummary[] | undefined
}) {
  const t = useTranslations('marketplace.detail')
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="border border-border bg-card">
        <CardHeader>
          <CardTitle className="text-sm">{t('versions.title')}</CardTitle>
        </CardHeader>
        <CardContent>
          {!versions || versions.length === 0 ? (
            <EmptyState
              iconId={item.icon_id ?? getDomainIconIdForResource(item.resource_type)}
              title={t('versions.emptyTitle')}
              description={t('versions.emptyDescription')}
            />
          ) : (
            <ul className="space-y-2 text-sm">
              {versions.map((v) => (
                <li
                  key={v.id}
                  className="flex items-center justify-between gap-3 rounded-md border border-border/60 px-3 py-2"
                >
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium">v{v.version_label}</span>
                      <Badge variant="secondary" className="moldy-ui-micro">
                        #{v.version_number}
                      </Badge>
                      {v.id === item.latest_version?.id ? (
                        <Badge className="bg-status-success/10 text-status-success moldy-ui-micro">
                          {t('versions.latest')}
                        </Badge>
                      ) : null}
                    </div>
                    {shortHash(v.content_hash) ? (
                      <code className="font-mono moldy-ui-caption text-muted-foreground">
                        {shortHash(v.content_hash)}
                      </code>
                    ) : null}
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatMediumDate(v.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card className="border border-border bg-card">
        <CardHeader>
          <CardTitle className="text-sm">{t('executionProfile.title')}</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          {item.execution_profile ? (
            <dl className="grid gap-2">
              {Object.entries(item.execution_profile).map(([key, value]) => (
                <div
                  key={key}
                  className="grid gap-1 rounded-md bg-muted/40 px-3 py-2 sm:grid-cols-[120px_1fr]"
                >
                  <dt className="font-medium text-foreground">
                    {t(`executionProfile.labels.${key}`) || key}
                  </dt>
                  <dd>{formatExecutionProfileValue(key, value, t)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p>{t('executionProfile.empty')}</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
