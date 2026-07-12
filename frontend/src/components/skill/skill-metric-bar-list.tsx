'use client'

/**
 * Phase 3 평가 지표 바 리스트 — usage `SpendBarChart`와 동일한 plain-div 바
 * 패턴(데이터 주도 width 인라인 스타일, check-design-system.mjs allowlist).
 * A/B 벤치마크 비교와 버전별 통과율 추이가 공유한다.
 */

export type SkillMetricBarRow = {
  readonly key: string
  readonly label: string
  /** 0..1 — bar fill ratio. */
  readonly ratio: number
  readonly display: string
  readonly tone: 'primary' | 'baseline'
  readonly meta?: string | null
}

function clampRatio(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(1, value))
}

export function SkillMetricBarList({
  rows,
  testId,
}: {
  readonly rows: readonly SkillMetricBarRow[]
  readonly testId?: string
}) {
  return (
    <div className="space-y-2" data-testid={testId}>
      {rows.map((row) => {
        const widthPct = Math.max(2, clampRatio(row.ratio) * 100)
        return (
          <div key={row.key} className="flex items-center gap-2" data-testid="skill-metric-bar">
            <span
              className="w-32 shrink-0 truncate moldy-ui-caption text-foreground/80"
              title={row.label}
            >
              {row.label}
            </span>
            <div className="relative h-5 flex-1 overflow-hidden rounded-md bg-muted/40">
              <div
                className="moldy-usage-bar h-full rounded-md transition-[width]"
                data-usage-metric={row.tone === 'primary' ? 'tokens' : 'baseline'}
                style={{ width: `${widthPct}%` }}
              />
            </div>
            <span className="w-24 shrink-0 text-right font-mono moldy-ui-caption tabular-nums text-foreground/90">
              {row.display}
            </span>
            {row.meta ? (
              <span className="w-20 shrink-0 truncate text-right moldy-ui-micro text-muted-foreground">
                {row.meta}
              </span>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
