'use client'

/**
 * Top-N target spend chart (M10).
 *
 * Companion to SpendLineChart. Used when the user picks `group_by='target'`
 * to compare cost / tokens / requests across agents or models.
 *
 * Plain SVG, sorted desc by metric, top N (default 10) with a single label
 * column on the left. Same minimal-impact rationale as SpendLineChart.
 */

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'

import type { UsageDailyEntry, UsageMetric } from '@/lib/types'

import { formatCostUsd, formatRequests, formatTokens } from './format'
import { UsageChartEmpty, UsageChartFrame } from './usage-chart-frame'

interface SpendBarChartProps {
  data: UsageDailyEntry[]
  metric: UsageMetric
  /** Number of top targets to render. Defaults to 10. */
  limit?: number
  className?: string
  label?: string
}

function metricValue(entry: UsageDailyEntry, metric: UsageMetric): number {
  if (metric === 'cost') return entry.total_cost_usd
  if (metric === 'tokens') return entry.total_tokens_in + entry.total_tokens_out
  return entry.request_count
}

function formatMetric(value: number, metric: UsageMetric): string {
  if (metric === 'cost') return formatCostUsd(value)
  if (metric === 'tokens') return formatTokens(value)
  return formatRequests(value)
}

export function SpendBarChart({
  data,
  metric,
  limit = 10,
  className,
  label,
}: SpendBarChartProps) {
  const t = useTranslations('usage.charts')
  const ranked = useMemo(() => {
    return [...data]
      .sort((a, b) => metricValue(b, metric) - metricValue(a, metric))
      .slice(0, limit)
  }, [data, metric, limit])

  if (ranked.length === 0) {
    return (
      <UsageChartEmpty className={className} testId="spend-bar-chart-empty">
        {t('empty')}
      </UsageChartEmpty>
    )
  }

  const max = Math.max(...ranked.map((d) => metricValue(d, metric)), 0.0001)
  const heading = label ?? t(`barMetric.${metric}`)

  return (
    <UsageChartFrame
      title={heading}
      meta={
        <>
          top {ranked.length} of {data.length}
        </>
      }
      className={className}
      testId="spend-bar-chart"
    >
      <div className="space-y-2">
        {ranked.map((entry, i) => {
          const v = metricValue(entry, metric)
          const widthPct = Math.max(2, (v / max) * 100)
          const labelText =
            entry.target_label ?? entry.target_id ?? `unknown-${i}`
          return (
            <div
              key={`${entry.target_id ?? 'none'}-${i}`}
              className="flex items-center gap-2"
              data-testid="spend-bar-row"
            >
              <span
                className="w-32 shrink-0 truncate moldy-ui-caption text-foreground/80"
                title={labelText}
              >
                {labelText}
              </span>
              <div className="relative h-5 flex-1 overflow-hidden rounded-md bg-muted/40">
                <div
                  className="moldy-usage-bar h-full rounded-md transition-[width]"
                  data-usage-metric={metric}
                  style={{ width: `${widthPct}%` }}
                />
              </div>
              <span className="w-24 shrink-0 text-right font-mono moldy-ui-caption tabular-nums text-foreground/90">
                {formatMetric(v, metric)}
              </span>
            </div>
          )
        })}
      </div>
    </UsageChartFrame>
  )
}
