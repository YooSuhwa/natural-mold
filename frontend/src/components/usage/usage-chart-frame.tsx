'use client'

import type { ReactNode } from 'react'

import type { UsageMetric } from '@/lib/types'
import { cn } from '@/lib/utils'

export const USAGE_METRIC_ACCENT: Record<UsageMetric, string> = {
  cost: 'var(--usage-chart-cost)',
  tokens: 'var(--usage-chart-tokens)',
  requests: 'var(--usage-chart-requests)',
}

interface UsageChartFrameProps {
  title: string
  meta: ReactNode
  children: ReactNode
  className?: string
  testId: string
}

export function UsageChartFrame({
  title,
  meta,
  children,
  className,
  testId,
}: UsageChartFrameProps) {
  return (
    <section className={cn('moldy-chart-panel', className)} data-testid={testId}>
      <div className="mb-3 flex min-w-0 items-baseline justify-between gap-3">
        <h4 className="min-w-0 truncate text-sm font-semibold text-foreground">{title}</h4>
        <p className="shrink-0 moldy-ui-micro tabular-nums text-muted-foreground">{meta}</p>
      </div>
      {children}
    </section>
  )
}

export function UsageChartEmpty({
  children,
  className,
  testId,
}: {
  children: ReactNode
  className?: string
  testId: string
}) {
  return (
    <div
      className={cn(
        'rounded-lg border border-dashed bg-muted/20 p-6 text-center text-xs text-muted-foreground',
        className,
      )}
      data-testid={testId}
    >
      {children}
    </div>
  )
}
