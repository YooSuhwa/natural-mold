'use client'

/**
 * Daily spend trend chart (M10).
 *
 * Plain SVG by design — the M9 health-history-chart establishes the pattern
 * (viewBox + path, < 100 data points, status-coloured points) and adding a
 * 60kB chart dependency for one line + one path violates "Minimal Impact".
 *
 * Renders a single metric (cost / tokens / requests) over time. Entries are
 * expected to be sorted ascending by date, but we tolerate gaps by treating
 * missing dates as zero rather than breaking the line.
 */

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'

import type { UsageDailyEntry, UsageMetric } from '@/lib/types'

import { formatCostUsd, formatRequests, formatTokens } from './format'
import { USAGE_METRIC_ACCENT, UsageChartEmpty, UsageChartFrame } from './usage-chart-frame'

interface SpendLineChartProps {
  data: UsageDailyEntry[]
  metric: UsageMetric
  className?: string
  /** Optional override of the chart label shown above the y-axis hint. */
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

export function SpendLineChart({ data, metric, className, label }: SpendLineChartProps) {
  const t = useTranslations('usage.charts')
  const sorted = useMemo(() => {
    return [...data]
      .filter((d): d is UsageDailyEntry & { date: string } => typeof d.date === 'string')
      .sort((a, b) => a.date.localeCompare(b.date))
  }, [data])

  if (sorted.length === 0) {
    return (
      <UsageChartEmpty className={className} testId="spend-line-chart-empty">
        {t('empty')}
      </UsageChartEmpty>
    )
  }

  const W = 720
  const H = 220
  const PAD_X = 44
  const PAD_Y = 20

  const values = sorted.map((d) => metricValue(d, metric))
  const max = Math.max(...values, 0.0001)
  const min = 0

  const xStep = sorted.length > 1 ? (W - PAD_X * 2) / (sorted.length - 1) : 0
  const yScale = (v: number) => {
    const t = (v - min) / Math.max(max - min, 0.0001)
    return H - PAD_Y - t * (H - PAD_Y * 2)
  }

  const linePath = sorted
    .map((d, i) => {
      const x = PAD_X + xStep * i
      const y = yScale(metricValue(d, metric))
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' ')

  const areaPath =
    sorted.length > 0
      ? `${linePath} L ${(PAD_X + xStep * (sorted.length - 1)).toFixed(1)} ${(H - PAD_Y).toFixed(
          1,
        )} L ${PAD_X.toFixed(1)} ${(H - PAD_Y).toFixed(1)} Z`
      : ''

  // 4 evenly spaced y-axis ticks for context.
  const ticks = [0, 0.33, 0.66, 1].map((t) => min + t * (max - min))

  const accent = USAGE_METRIC_ACCENT[metric]
  const heading = label ?? t(`lineMetric.${metric}`)

  return (
    <UsageChartFrame
      title={heading}
      meta={
        <>
          {sorted.length} day{sorted.length === 1 ? '' : 's'} · max {formatMetric(max, metric)}
        </>
      }
      className={className}
      testId="spend-line-chart"
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-56 w-full"
        role="img"
        aria-label={`${heading} trend chart`}
      >
        {/* Y-axis grid */}
        {ticks.map((tick, i) => {
          const y = yScale(tick)
          return (
            <g key={i}>
              <line
                x1={PAD_X}
                y1={y}
                x2={W - PAD_X}
                y2={y}
                stroke="currentColor"
                strokeOpacity="0.08"
                strokeWidth="1"
              />
              <text
                x={PAD_X - 6}
                y={y + 3}
                textAnchor="end"
                className="fill-muted-foreground moldy-ui-nano"
              >
                {formatMetric(tick, metric)}
              </text>
            </g>
          )
        })}
        {/* Filled area under the curve */}
        <path d={areaPath} fill={accent} fillOpacity="0.12" />
        {/* Line */}
        <path
          d={linePath}
          fill="none"
          stroke={accent}
          strokeWidth="1.75"
          strokeLinejoin="round"
        />
        {/* Points with native tooltip */}
        {sorted.map((d, i) => {
          const x = PAD_X + xStep * i
          const v = metricValue(d, metric)
          const y = yScale(v)
          return (
            <circle
              key={d.date}
              cx={x}
              cy={y}
              r={2.5}
              fill={accent}
              stroke="white"
              strokeWidth="1"
              data-testid="spend-line-point"
            >
              <title>
                {`${d.date} · ${formatMetric(v, metric)}`}
              </title>
            </circle>
          )
        })}
      </svg>
      {/* X-axis labels — show first / mid / last to avoid overlap */}
      {sorted.length > 0 && (
        <div className="mt-1 flex justify-between moldy-ui-micro text-muted-foreground">
          <span>{sorted[0].date}</span>
          {sorted.length > 2 && <span>{sorted[Math.floor(sorted.length / 2)].date}</span>}
          {sorted.length > 1 && <span>{sorted[sorted.length - 1].date}</span>}
        </div>
      )}
    </UsageChartFrame>
  )
}
