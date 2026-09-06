'use client'

import { UsageChartEmpty, UsageChartFrame } from '@/components/usage/usage-chart-frame'
import { MoldyChartSvg } from '@/components/ui/chart-svg'

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

/**
 * Phase 2 generative-UI component: renders a typed ``chart`` payload as a plain
 * SVG line or bar chart (the codebase pattern — no chart.js for simple series).
 * Generic ``{label, value}`` series, reusing the usage chart frame shell.
 */
export function ChartCard({ chartType, series, title, xLabel, yLabel }: ChartCardProps) {
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

  return (
    <div className="my-2 max-w-xl" data-testid="data-ui-chart" data-chart-type={chartType}>
      <UsageChartFrame title={resolvedTitle} meta={yLabel ?? ''} testId="data-ui-chart-frame">
        <MoldyChartSvg
          ariaLabel={resolvedTitle}
          className="h-44 w-full text-primary"
          series={series}
          variant={chartType}
        />
        {xLabel ? <p className="mt-1 text-center text-xs text-muted-foreground">{xLabel}</p> : null}
      </UsageChartFrame>
    </div>
  )
}
