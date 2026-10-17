'use client'

import { useTranslations } from 'next-intl'
import { Eye, Lightbulb, Wrench } from 'lucide-react'

import { StatusChip } from '@/components/shared/status-chip'
import type { Model } from '@/lib/types/model'
import type { HealthCheckEntry } from '@/lib/types/health'

export function HealthCell({
  entry,
  format,
}: {
  entry: HealthCheckEntry | undefined
  format: (iso: string) => string
}) {
  if (!entry) {
    return <StatusChip variant="unknown" />
  }
  return (
    <div className="flex flex-col items-start gap-0.5">
      <StatusChip variant={entry.status} />
      <span className="moldy-ui-micro text-muted-foreground">{format(entry.checked_at)}</span>
    </div>
  )
}

export function formatRelativeTime(
  iso: string,
  t: (
    key:
      | 'relativeTime.secondsAgo'
      | 'relativeTime.minutesAgo'
      | 'relativeTime.hoursAgo'
      | 'relativeTime.daysAgo',
    values: { count: number },
  ) => string,
): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const deltaSec = Math.floor((Date.now() - then) / 1000)
  if (deltaSec < 60) return t('relativeTime.secondsAgo', { count: deltaSec })
  if (deltaSec < 3600) return t('relativeTime.minutesAgo', { count: Math.floor(deltaSec / 60) })
  if (deltaSec < 86400) return t('relativeTime.hoursAgo', { count: Math.floor(deltaSec / 3600) })
  return t('relativeTime.daysAgo', { count: Math.floor(deltaSec / 86400) })
}

/**
 * True when at least one benchmark score is present. Used by the "Has
 * ranking" toggle so we can hide unmatched/Custom-ID models on demand.
 */
export function modelHasAnyRanking(model: Model): boolean {
  const r = model.rankings
  if (!r) return false
  return (
    typeof r.lmarena === 'number' ||
    typeof r.livebench === 'number' ||
    typeof r.aa_index === 'number'
  )
}

export function CapabilityIcons({ model }: { model: Model }) {
  const t = useTranslations('model')
  const items = [
    {
      key: 'vision',
      icon: Eye,
      enabled: Boolean(model.supports_vision),
      title: t('vision'),
    },
    {
      key: 'tools',
      icon: Wrench,
      enabled: Boolean(model.supports_function_calling),
      title: t('functionCalling'),
    },
    {
      key: 'reasoning',
      icon: Lightbulb,
      enabled: Boolean(model.supports_reasoning),
      title: t('reasoning'),
    },
  ].filter((i) => i.enabled)

  if (items.length === 0) return null

  return (
    <div className="mt-0.5 flex items-center gap-1 text-muted-foreground">
      {items.map(({ key, icon: Icon, title }) => (
        <Icon key={key} className="size-3" aria-label={title} />
      ))}
    </div>
  )
}
