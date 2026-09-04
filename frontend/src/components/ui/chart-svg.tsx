import { cn } from '@/lib/utils'

export interface MoldyChartPoint {
  readonly label: string
  readonly value: number
}

interface MoldyChartSvgProps {
  readonly ariaLabel: string
  readonly className?: string
  readonly series: readonly MoldyChartPoint[]
  readonly variant: 'bar' | 'line'
}

const CHART_PALETTE = [
  '#6366f1',
  '#10b981',
  '#f59e0b',
  '#ef4444',
  '#3b82f6',
  '#a855f7',
  '#ec4899',
  '#14b8a6',
] as const

const VIEW_W = 480
const VIEW_H = 180
const PAD = { top: 10, right: 12, bottom: 26, left: 12 }
const PLOT_W = VIEW_W - PAD.left - PAD.right
const PLOT_H = VIEW_H - PAD.top - PAD.bottom

function seriesColor(index: number): string {
  return CHART_PALETTE[index % CHART_PALETTE.length]
}

function pointX(index: number, count: number): number {
  if (count <= 1) return PAD.left + PLOT_W / 2
  return PAD.left + (index / (count - 1)) * PLOT_W
}

function valueY(value: number, max: number): number {
  const baseline = PAD.top + PLOT_H
  if (max <= 0) return baseline
  return baseline - Math.min(1, Math.max(0, value / max)) * PLOT_H
}

/** Shared accessible SVG renderer for compact data-driven line and bar charts. */
export function MoldyChartSvg({ ariaLabel, className, series, variant }: MoldyChartSvgProps) {
  const max = Math.max(0, ...series.map((point) => point.value))
  const barWidth = (PLOT_W / series.length) * 0.6
  const linePoints = series
    .map((point, index) => `${pointX(index, series.length)},${valueY(point.value, max)}`)
    .join(' ')

  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      className={cn('moldy-chart-svg', className)}
      role="img"
      aria-label={ariaLabel}
      preserveAspectRatio="none"
    >
      {variant === 'bar' ? (
        series.map((point, index) => {
          const x = pointX(index, series.length) - barWidth / 2
          const y = valueY(point.value, max)
          return (
            <rect
              key={`${point.label}-${index}`}
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
              key={`${point.label}-${index}`}
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
          key={`${point.label}-${index}`}
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
  )
}
