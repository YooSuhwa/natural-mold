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
  healthy: 'rgb(16 185 129)', // emerald-500
  degraded: 'rgb(245 158 11)', // amber-500
  unhealthy: 'rgb(244 63 94)', // rose-500
  unknown: 'rgb(148 163 184)', // slate-400
}

const STATUS_BG: Record<HealthStatus, string> = {
  healthy: 'bg-emerald-500',
  degraded: 'bg-amber-500',
  unhealthy: 'bg-rose-500',
  unknown: 'bg-slate-400',
}

export function HealthHistoryChart({
  targetKind,
  targetId,
  limit = 30,
  className,
}: HealthHistoryChartProps) {
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
        Failed to load health history.
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
        Health check 기록 없음 — &quot;Check now&quot; 버튼으로 시작하세요.
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
        Latency 데이터가 없는 probe입니다 (모두 실패).
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
        <h4 className="text-xs font-semibold text-foreground">Latency (ms)</h4>
        <p className="text-[10px] text-muted-foreground">
          min {Math.round(stats.min)} · max {Math.round(stats.max)} · {entries.length} probes
        </p>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-32 w-full"
        role="img"
        aria-label="Latency history line chart"
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
                {`${formatRelativeTime(e.checked_at)} · ${e.status} · ${e.latency_ms}ms`}
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
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="mb-2 flex items-baseline justify-between">
        <h4 className="text-xs font-semibold text-foreground">Status timeline</h4>
        <p className="text-[10px] text-muted-foreground">oldest → newest</p>
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
                  aria-label={`${e.status} probe at ${e.checked_at}`}
                />
              }
            />
            <TooltipContent>
              <div className="space-y-0.5 text-left">
                <p className="font-medium">
                  {e.status} · {formatRelativeTime(e.checked_at)}
                </p>
                {typeof e.latency_ms === 'number' && (
                  <p className="text-[10px] opacity-80">Latency: {e.latency_ms}ms</p>
                )}
                {e.error_kind && (
                  <p className="text-[10px] opacity-80">
                    {e.error_kind}: {e.error_message ?? 'no message'}
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
  const items: { status: HealthStatus; label: string }[] = [
    { status: 'healthy', label: 'Healthy' },
    { status: 'degraded', label: 'Degraded' },
    { status: 'unhealthy', label: 'Unhealthy' },
    { status: 'unknown', label: 'Unknown' },
  ]
  return (
    <div className="flex flex-wrap items-center gap-3 text-[10px] text-muted-foreground">
      {items.map((i) => (
        <span key={i.status} className="inline-flex items-center gap-1">
          <span className={cn('inline-block size-2 rounded-sm', STATUS_BG[i.status])} />
          {i.label}
        </span>
      ))}
    </div>
  )
}

// -- Helpers ----------------------------------------------------------------

function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const now = Date.now()
  const deltaSec = Math.floor((now - then) / 1000)
  if (deltaSec < 60) return `${deltaSec}s ago`
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`
  return `${Math.floor(deltaSec / 86400)}d ago`
}
