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

import type { UsageDailyEntry, UsageMetric } from '@/lib/types'
import { cn } from '@/lib/utils'

import { formatCostUsd, formatRequests, formatTokens } from './format'

interface SpendBarChartProps {
  data: UsageDailyEntry[]
  metric: UsageMetric
  /** Number of top targets to render. Defaults to 10. */
  limit?: number
  className?: string
  label?: string
}

const METRIC_LABEL: Record<UsageMetric, string> = {
  cost: 'Cost (USD) by target',
  tokens: 'Tokens by target',
  requests: 'Requests by target',
}

const METRIC_ACCENT: Record<UsageMetric, string> = {
  cost: 'rgb(16 185 129)',
  tokens: 'rgb(59 130 246)',
  requests: 'rgb(168 85 247)',
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
  const ranked = useMemo(() => {
    return [...data]
      .sort((a, b) => metricValue(b, metric) - metricValue(a, metric))
      .slice(0, limit)
  }, [data, metric, limit])

  if (ranked.length === 0) {
    return (
      <div
        className={cn(
          'rounded-lg border border-dashed bg-muted/20 p-6 text-center text-xs text-muted-foreground',
          className,
        )}
        data-testid="spend-bar-chart-empty"
      >
        데이터 없음 — 에이전트 실행 후 표시됩니다
      </div>
    )
  }

  const max = Math.max(...ranked.map((d) => metricValue(d, metric)), 0.0001)
  const accent = METRIC_ACCENT[metric]
  const heading = label ?? METRIC_LABEL[metric]

  return (
    <div
      className={cn('rounded-lg border bg-card p-4', className)}
      data-testid="spend-bar-chart"
    >
      <div className="mb-3 flex items-baseline justify-between">
        <h4 className="text-sm font-semibold text-foreground">{heading}</h4>
        <p className="text-[10px] text-muted-foreground">
          top {ranked.length} of {data.length}
        </p>
      </div>
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
                className="w-32 shrink-0 truncate text-[11px] text-foreground/80"
                title={labelText}
              >
                {labelText}
              </span>
              <div className="relative h-5 flex-1 overflow-hidden rounded-md bg-muted/40">
                <div
                  className="h-full rounded-md transition-[width]"
                  style={{ width: `${widthPct}%`, backgroundColor: accent }}
                />
              </div>
              <span className="w-24 shrink-0 text-right font-mono text-[11px] tabular-nums text-foreground/90">
                {formatMetric(v, metric)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
