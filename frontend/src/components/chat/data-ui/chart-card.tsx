'use client'

import { useMemo } from 'react'
import { UsageChartEmpty, UsageChartFrame } from '@/components/usage/usage-chart-frame'

export interface ChartSeriesPoint {
  label: string
  value: number
}

export interface ChartCardProps {
  chartType: 'line' | 'bar'
  series: ChartSeriesPoint[]
  title?: string
  xLabel?: string
  yLabel?: string
}

// Distinct, accessible categorical palette so a bar chart's categories are
// visually separable (a single ``currentColor`` made every bar the same mint).
const CHART_PALETTE = [
  '#6366f1', // indigo
  '#10b981', // emerald
  '#f59e0b', // amber
  '#ef4444', // red
  '#3b82f6', // blue
  '#a855f7', // purple
  '#ec4899', // pink
  '#14b8a6', // teal
] as const

function seriesColor(index: number): string {
  return CHART_PALETTE[index % CHART_PALETTE.length]
}

const VIEW_W = 480
const VIEW_H = 180
const PAD = { top: 10, right: 12, bottom: 26, left: 12 }
const PLOT_W = VIEW_W - PAD.left - PAD.right
const PLOT_H = VIEW_H - PAD.top - PAD.bottom

function pointX(index: number, count: number): number {
  if (count <= 1) return PAD.left + PLOT_W / 2
  return PAD.left + (index / (count - 1)) * PLOT_W
}

function valueY(value: number, max: number): number {
  const baseline = PAD.top + PLOT_H
  if (max <= 0) return baseline
  // Clamp to the plot box so negative/over-max values can't produce invalid SVG
  // geometry (a negative-height rect / a point outside the viewBox). Negatives
  // render at the baseline (this chart assumes a non-negative range).
  const fraction = Math.min(1, Math.max(0, value / max))
  return baseline - fraction * PLOT_H
}

/**
 * Phase 2 generative-UI component: renders a typed ``chart`` payload as a plain
 * SVG line or bar chart (the codebase pattern — no chart.js for simple series).
 * Generic ``{label, value}`` series, reusing the usage chart frame shell.
 */
export function ChartCard({ chartType, series, title, xLabel, yLabel }: ChartCardProps) {
  const max = useMemo(() => Math.max(0, ...series.map((point) => point.value)), [series])
  const resolvedTitle = title ?? ''

  if (series.length === 0) {
    return (
      <div className="my-2 max-w-xl" data-testid="data-ui-chart">
        <UsageChartFrame title={resolvedTitle} meta={yLabel ?? ''} testId="data-ui-chart-frame">
          <UsageChartEmpty testId="data-ui-chart-empty">—</UsageChartEmpty>
        </UsageChartFrame>
      </div>
    )
  }

  const barWidth = (PLOT_W / series.length) * 0.6
  const linePoints = series
    .map((point, index) => `${pointX(index, series.length)},${valueY(point.value, max)}`)
    .join(' ')

  return (
    <div className="my-2 max-w-xl" data-testid="data-ui-chart" data-chart-type={chartType}>
      <UsageChartFrame title={resolvedTitle} meta={yLabel ?? ''} testId="data-ui-chart-frame">
        <svg
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          className="h-44 w-full text-primary"
          role="img"
          aria-label={resolvedTitle}
          preserveAspectRatio="none"
        >
          {chartType === 'bar' ? (
            series.map((point, index) => {
              const x = pointX(index, series.length) - barWidth / 2
              const y = valueY(point.value, max)
              return (
                <rect
                  key={index}
                  x={x}
                  y={y}
                  width={barWidth}
                  height={PAD.top + PLOT_H - y}
                  rx={2}
                  fill={seriesColor(index)}
                  opacity={0.9}
                />
              )
            })
          ) : (
            <>
              <polyline
                points={linePoints}
                fill="none"
                stroke={seriesColor(0)}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {series.map((point, index) => (
                <circle
                  key={index}
                  cx={pointX(index, series.length)}
                  cy={valueY(point.value, max)}
                  r={3}
                  fill={seriesColor(index)}
                />
              ))}
            </>
          )}
          {series.map((point, index) => (
            <text
              key={index}
              x={pointX(index, series.length)}
              y={VIEW_H - 8}
              textAnchor="middle"
              className="fill-muted-foreground"
              fontSize={11}
            >
              {point.label}
            </text>
          ))}
        </svg>
        {xLabel ? <p className="mt-1 text-center text-xs text-muted-foreground">{xLabel}</p> : null}
      </UsageChartFrame>
    </div>
  )
}
