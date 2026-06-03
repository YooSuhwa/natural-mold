'use client'

/**
 * Health history visualization (M9). Two stacked panels:
 *   1. Latency line chart — last N probe latencies, status-coloured points.
 *   2. Status timeline strip — N coloured cells (healthy/degraded/unhealthy).
 *
 * We render plain SVG instead of pulling in a chart library because the data
 * shape is trivial (single series, < 100 points) and adding a 60kB dep for two
 * lines/strips would violate "Minimal Impact". Dimensions are responsive via
 * viewBox.
 */

import { useMemo } from 'react'
import { Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useHealthHistory } from '@/lib/hooks/use-health'
import type { HealthCheckEntry, HealthStatus, HealthTargetKind } from '@/lib/types/health'
import { cn } from '@/lib/utils'

interface HealthHistoryChartProps {
  targetKind: HealthTargetKind
  targetId: string
  limit?: number
  className?: string
}

const STATUS_COLOR: Record<HealthStatus, string> = {
  healthy: 'var(--status-success)',
  degraded: 'var(--status-warn)',
  unhealthy: 'var(--status-danger)',
  unknown: 'var(--muted-foreground)',
}

const STATUS_BG: Record<HealthStatus, string> = {
  healthy: 'bg-status-success',
  degraded: 'bg-status-warn',
  unhealthy: 'bg-status-danger',
  unknown: 'bg-muted-foreground/60',
}

export function HealthHistoryChart({
  targetKind,
  targetId,
  limit = 30,
  className,
}: HealthHistoryChartProps) {
  const t = useTranslations('shared.healthHistory')
  const { data, isLoading, isError } = useHealthHistory(targetKind, targetId, limit)

  if (isLoading) {
    return (
      <div
        className={cn(
          'flex items-center justify-center rounded-lg border bg-muted/30 py-8 text-xs text-muted-foreground',
          className,
        )}
      >
        <Loader2 className="size-4 animate-spin" />
      </div>
    )
  }

  if (isError) {
    return (
      <div
        className={cn(
          'rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive',
          className,
        )}
      >
        {t('loadFailed')}
      </div>
    )
  }

  const entries = data ?? []
  if (entries.length === 0) {
    return (
      <div
        className={cn(
          'rounded-lg border border-dashed bg-muted/20 p-4 text-center text-xs text-muted-foreground',
          className,
        )}
        data-testid="health-history-empty"
      >
        {t('empty')}
      </div>
    )
  }

  return (
    <div className={cn('space-y-3', className)} data-testid="health-history-chart">
      <LatencyLineChart entries={entries} />
      <StatusTimelineStrip entries={entries} />
      <Legend />
    </div>
  )
}

// -- Latency line chart -----------------------------------------------------

function LatencyLineChart({ entries }: { entries: HealthCheckEntry[] }) {
  const t = useTranslations('shared.healthHistory')
  // viewBox-based geometry so the chart scales to its container width.
  const W = 600
  const H = 120
  const PAD_X = 28
  const PAD_Y = 12

  const stats = useMemo(() => {
    const latencies = entries
      .map((e) => e.latency_ms)
      .filter((v): v is number => typeof v === 'number')
    if (latencies.length === 0) return null
    const max = Math.max(...latencies, 1)
    const min = Math.min(...latencies, 0)
    return { max, min, range: Math.max(max - min, 1) }
  }, [entries])

  if (!stats) {
    return (
      <div className="rounded-lg border bg-muted/20 p-4 text-center text-xs text-muted-foreground">
        {t('noLatency')}
      </div>
    )
  }

  const xStep = entries.length > 1 ? (W - PAD_X * 2) / (entries.length - 1) : 0
  const yScale = (latency: number) => {
    const t = (latency - stats.min) / stats.range
    return H - PAD_Y - t * (H - PAD_Y * 2)
  }

  // Build the polyline from points that have latency. Points without latency
  // (e.g. timeouts that returned null) break the line into segments.
  const pathSegments: string[] = []
  let currentSegment: string[] = []
  entries.forEach((e, i) => {
    if (typeof e.latency_ms !== 'number') {
      if (currentSegment.length > 0) {
        pathSegments.push(currentSegment.join(' '))
        currentSegment = []
      }
      return
    }
    const x = PAD_X + xStep * i
    const y = yScale(e.latency_ms)
    currentSegment.push(
      `${currentSegment.length === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`,
    )
  })
  if (currentSegment.length > 0) pathSegments.push(currentSegment.join(' '))

  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="mb-2 flex items-baseline justify-between">
        <h4 className="text-xs font-semibold text-foreground">{t('latencyTitle')}</h4>
        <p className="moldy-ui-micro text-muted-foreground">
          {t('summary', {
            min: Math.round(stats.min),
            max: Math.round(stats.max),
            count: entries.length,
          })}
        </p>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-32 w-full"
        role="img"
        aria-label={t('chartLabel')}
      >
        {/* Y-axis baseline */}
        <line
          x1={PAD_X}
          y1={H - PAD_Y}
          x2={W - PAD_X}
          y2={H - PAD_Y}
          stroke="currentColor"
          strokeOpacity="0.1"
          strokeWidth="1"
        />
        {/* Latency line(s) */}
        {pathSegments.map((d, i) => (
          <path
            key={i}
            d={d}
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeOpacity="0.5"
            strokeLinejoin="round"
          />
        ))}
        {/* Status-coloured points */}
        {entries.map((e, i) => {
          if (typeof e.latency_ms !== 'number') return null
          const x = PAD_X + xStep * i
          const y = yScale(e.latency_ms)
          return (
            <circle
              key={e.id}
              cx={x}
              cy={y}
              r={3}
              fill={STATUS_COLOR[e.status]}
              stroke="white"
              strokeWidth="1"
            >
              <title>
                {t('pointTitle', {
                  relative: formatRelativeTime(e.checked_at, t),
                  status: t(`status.${e.status}`),
                  latency: e.latency_ms,
                })}
              </title>
            </circle>
          )
        })}
      </svg>
    </div>
  )
}

// -- Status timeline strip --------------------------------------------------

function StatusTimelineStrip({ entries }: { entries: HealthCheckEntry[] }) {
  const t = useTranslations('shared.healthHistory')
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="mb-2 flex items-baseline justify-between">
        <h4 className="text-xs font-semibold text-foreground">{t('statusTimeline')}</h4>
        <p className="moldy-ui-micro text-muted-foreground">{t('oldestNewest')}</p>
      </div>
      <div className="flex items-stretch gap-0.5" data-testid="status-timeline">
        {entries.map((e) => (
          <Tooltip key={e.id}>
            <TooltipTrigger
              render={
                <span
                  data-status={e.status}
                  className={cn(
                    'h-6 flex-1 cursor-pointer rounded-sm transition-opacity hover:opacity-80',
                    STATUS_BG[e.status],
                  )}
                  aria-label={t('probeAt', {
                    status: t(`status.${e.status}`),
                    checkedAt: e.checked_at,
                  })}
                />
              }
            />
            <TooltipContent>
              <div className="space-y-0.5 text-left">
                <p className="font-medium">
                  {t(`status.${e.status}`)} · {formatRelativeTime(e.checked_at, t)}
                </p>
                {typeof e.latency_ms === 'number' && (
                  <p className="moldy-ui-micro opacity-80">
                    {t('latencyValue', { value: e.latency_ms })}
                  </p>
                )}
                {e.error_kind && (
                  <p className="moldy-ui-micro opacity-80">
                    {e.error_kind}: {e.error_message ?? t('noMessage')}
                  </p>
                )}
              </div>
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
    </div>
  )
}

// -- Legend -----------------------------------------------------------------

function Legend() {
  const t = useTranslations('shared.healthHistory')
  const items: { status: HealthStatus; labelKey: string }[] = [
    { status: 'healthy', labelKey: 'status.healthy' },
    { status: 'degraded', labelKey: 'status.degraded' },
    { status: 'unhealthy', labelKey: 'status.unhealthy' },
    { status: 'unknown', labelKey: 'status.unknown' },
  ]
  return (
    <div className="flex flex-wrap items-center gap-3 moldy-ui-micro text-muted-foreground">
      {items.map((i) => (
        <span key={i.status} className="inline-flex items-center gap-1">
          <span className={cn('inline-block size-2 rounded-sm', STATUS_BG[i.status])} />
          {t(i.labelKey)}
        </span>
      ))}
    </div>
  )
}

// -- Helpers ----------------------------------------------------------------

function formatRelativeTime(
  iso: string,
  t: ReturnType<typeof useTranslations<'shared.healthHistory'>>,
): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const now = Date.now()
  const deltaSec = Math.floor((now - then) / 1000)
  if (deltaSec < 60) return t('relative.seconds', { count: deltaSec })
  if (deltaSec < 3600) return t('relative.minutes', { count: Math.floor(deltaSec / 60) })
  if (deltaSec < 86400) return t('relative.hours', { count: Math.floor(deltaSec / 3600) })
  return t('relative.days', { count: Math.floor(deltaSec / 86400) })
}
