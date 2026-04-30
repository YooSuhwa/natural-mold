'use client'

import { Info } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { ModelRankingKey, ModelRankings } from '@/lib/types/model'

/**
 * M11 — External benchmark labels and tooltip copy. Centralised so the table
 * header, edit dialog, and discover panel stay in sync.
 *
 * Backend cron refreshes the underlying snapshots ~every 6 hours; missing
 * scores are rendered as a muted em-dash everywhere.
 */
export const RANKING_META: Record<
  ModelRankingKey,
  { label: string; short: string; tooltip: string; format: 'integer' | 'decimal' }
> = {
  lmarena: {
    label: 'LMArena',
    short: 'LMArena',
    tooltip:
      'LMArena Chatbot Arena ELO score (higher = better). Refreshed every ~6h.',
    format: 'integer',
  },
  livebench: {
    label: 'LiveBench',
    short: 'LiveBench',
    tooltip:
      'LiveBench composite score 0–100 (higher = better). Refreshed every ~6h.',
    format: 'decimal',
  },
  aa_index: {
    label: 'AA Index',
    short: 'AA',
    tooltip:
      'Artificial Analysis Intelligence Index 0–100 (higher = better). Refreshed every ~6h.',
    format: 'decimal',
  },
}

/**
 * Format a single ranking score. Integer for ELO-style scores, 1 decimal for
 * 0–100 composite scores. Returns null when the value is missing or invalid
 * so callers can choose how to render the empty state.
 */
export function formatRankingValue(
  value: number | null | undefined,
  format: 'integer' | 'decimal',
): string | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return format === 'integer' ? Math.round(value).toString() : value.toFixed(1)
}

interface RankingCellProps {
  value: number | null | undefined
  format: 'integer' | 'decimal'
  className?: string
}

/** Compact table cell — number or muted em-dash. */
export function RankingCell({ value, format, className }: RankingCellProps) {
  const formatted = formatRankingValue(value, format)
  if (formatted === null) {
    return (
      <span
        className={cn('text-xs text-muted-foreground', className)}
        aria-label="No ranking data"
      >
        —
      </span>
    )
  }
  return (
    <span className={cn('font-mono text-xs tabular-nums', className)}>{formatted}</span>
  )
}

interface RankingHeaderProps {
  rankingKey: ModelRankingKey
}

/** Column header label + ⓘ tooltip describing what the score represents. */
export function RankingHeader({ rankingKey }: RankingHeaderProps) {
  const meta = RANKING_META[rankingKey]
  return (
    <span className="inline-flex items-center gap-1">
      {meta.short}
      <Tooltip>
        <TooltipTrigger
          render={(triggerProps) => (
            <span
              {...triggerProps}
              role="img"
              aria-label={`${meta.label} info`}
              className="inline-flex cursor-help items-center text-muted-foreground"
              onClick={(e) => e.stopPropagation()}
            >
              <Info className="size-3" />
            </span>
          )}
        />
        <TooltipContent>{meta.tooltip}</TooltipContent>
      </Tooltip>
    </span>
  )
}

interface RankingsSectionProps {
  rankings: ModelRankings | null | undefined
  className?: string
  /**
   * Helper copy shown when every score is missing. Defaults to the standard
   * "no data — refreshed every ~6h" line; overridable for Custom-ID models
   * where the cron simply hasn't matched the model yet.
   */
  emptyHint?: string
}

/**
 * Card-grid summary used by ModelEditDialog. Shows every benchmark with its
 * tooltip, falls back to a single helper line when nothing is populated.
 */
export function RankingsSection({
  rankings,
  className,
  emptyHint,
}: RankingsSectionProps) {
  const items = (Object.keys(RANKING_META) as ModelRankingKey[]).map((key) => ({
    key,
    meta: RANKING_META[key],
    value: rankings?.[key],
  }))
  const hasAny = items.some((i) => formatRankingValue(i.value, i.meta.format) !== null)

  return (
    <section
      data-testid="model-rankings"
      className={cn('rounded-lg border bg-muted/30 p-3', className)}
    >
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold">Benchmark rankings</h3>
        <Tooltip>
          <TooltipTrigger
            render={(triggerProps) => (
              <span
                {...triggerProps}
                role="img"
                aria-label="Rankings info"
                className="inline-flex cursor-help items-center text-muted-foreground"
              >
                <Info className="size-3" />
              </span>
            )}
          />
          <TooltipContent>
            Public benchmark scores. Refreshed every ~6 hours by the catalog cron.
          </TooltipContent>
        </Tooltip>
      </div>

      <div className="grid grid-cols-3 gap-2">
        {items.map(({ key, meta, value }) => {
          const formatted = formatRankingValue(value, meta.format)
          return (
            <div
              key={key}
              data-ranking={key}
              className="flex flex-col gap-0.5 rounded-md border bg-background px-3 py-2"
            >
              <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {meta.label}
              </span>
              {formatted ? (
                <span className="font-mono text-sm tabular-nums">{formatted}</span>
              ) : (
                <span className="text-sm text-muted-foreground">—</span>
              )}
            </div>
          )
        })}
      </div>

      {!hasAny && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {emptyHint ??
            'No benchmark data yet — refreshed every ~6 hours by the catalog cron.'}
        </p>
      )}
    </section>
  )
}

interface RankingBadgeProps {
  rankingKey: ModelRankingKey
  value: number | null | undefined
  className?: string
}

/**
 * Tiny inline badge used in the discover-panel rows so users can spot strong
 * models at a glance. Hidden entirely when the score is missing.
 */
export function RankingBadge({ rankingKey, value, className }: RankingBadgeProps) {
  const meta = RANKING_META[rankingKey]
  const formatted = formatRankingValue(value, meta.format)
  if (formatted === null) return null
  return (
    <span
      data-ranking-badge={rankingKey}
      className={cn(
        'inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-foreground/80 ring-1 ring-inset ring-border',
        className,
      )}
      title={`${meta.label}: ${formatted}`}
    >
      <span className="font-sans uppercase tracking-wide text-muted-foreground">
        {meta.short}
      </span>
      {formatted}
    </span>
  )
}

/**
 * Highest-priority ranking comparator for sorting discover results. Models
 * with at least one non-null benchmark sort above models with none, and
 * within those LMArena → LiveBench → AA Index is the tie-break order.
 */
export function rankingScoreFor(
  rankings: ModelRankings | null | undefined,
): number {
  if (!rankings) return -1
  if (typeof rankings.lmarena === 'number') return rankings.lmarena
  if (typeof rankings.livebench === 'number') return rankings.livebench + 100_000
  if (typeof rankings.aa_index === 'number') return rankings.aa_index
  return -1
}
