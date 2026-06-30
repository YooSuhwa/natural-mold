'use client'

import { ArrowDownIcon, ArrowUpIcon } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

export interface StatItem {
  label: string
  value: string | number
  delta?: number
  unit?: string
}

export interface StatsCardProps {
  items: StatItem[]
}

function formatValue(value: string | number, unit?: string): string {
  const base = typeof value === 'number' ? value.toLocaleString() : value
  return unit ? `${base}${unit}` : base
}

/**
 * Phase 2 generative-UI component: renders a typed ``stats`` payload as a KPI
 * grid (label + value + optional unit + optional delta). Built on the shadcn
 * Card primitive; text-only values (R2: no raw HTML).
 */
export function StatsCard({ items }: StatsCardProps) {
  return (
    <div
      className="my-2 grid max-w-2xl grid-cols-2 gap-2 sm:grid-cols-3"
      data-testid="data-ui-stats"
    >
      {items.map((item, index) => (
        <Card key={`${item.label}-${index}`} className="gap-1 p-3">
          <p className="truncate text-xs text-muted-foreground">{item.label}</p>
          <p className="text-lg font-semibold tabular-nums text-foreground">
            {formatValue(item.value, item.unit)}
          </p>
          {typeof item.delta === 'number' ? (
            <span
              className={cn(
                'inline-flex items-center gap-0.5 text-xs tabular-nums',
                item.delta >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive',
              )}
            >
              {item.delta >= 0 ? (
                <ArrowUpIcon className="size-3" aria-hidden />
              ) : (
                <ArrowDownIcon className="size-3" aria-hidden />
              )}
              {Math.abs(item.delta)}%
            </span>
          ) : null}
        </Card>
      ))}
    </div>
  )
}
