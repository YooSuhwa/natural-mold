'use client'

import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { formatContextWindow } from '@/lib/utils/provider'
import { formatCompactCount, formatDisplayNumber } from '@/lib/utils/display-format'
import type { TokenUsageBreakdown } from '@/lib/types'

// ──────────────────────────────────────────────
// ContextWindowGauge — 컴포저 하단의 "컨텍스트 창 사용량" 표시(클로드코드式).
//
// 점유량 = 최신 assistant 턴의 ``prompt_tokens`` 단독. LangChain 1.x에서
// input_tokens는 cache 토큰을 모두 포함한 총 input이므로 cache_*를 더하지 않는다
// (더하면 이중계상). 한도 = model.context_window.
//
// context_window가 null인 모델은 숨기지 않고 "비활성" 상태로 — muted/점선 ring +
// 다른 색 + 호버 시 한도 미설정 안내. (사용량 표시 불가임을 명확히.)
// ──────────────────────────────────────────────

interface ContextWindowGaugeProps {
  /** 가장 최근 assistant 턴 usage (점유량은 prompt_tokens). 첫 턴 전이면 null. */
  readonly usage: TokenUsageBreakdown | null
  /** 모델 컨텍스트 창 한도(토큰). null이면 비활성. */
  readonly contextWindow: number | null | undefined
  /** 게이지 옆에 함께 표시할 모델명. */
  readonly modelName?: string
}

// 14px ring. r=6 → circumference 2π·6 ≈ 37.699.
const RING_RADIUS = 6
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS

/** 사용자 지정: 80%↑ 경고색, 95%↑ 위험색. 그 미만은 중립. */
function levelColorClass(hasLimit: boolean, percent: number): string {
  if (!hasLimit) return 'text-muted-foreground/50'
  if (percent >= 95) return 'text-status-danger'
  if (percent >= 80) return 'text-status-warn'
  return 'text-muted-foreground'
}

export function ContextWindowGauge({ usage, contextWindow, modelName }: ContextWindowGaugeProps) {
  const t = useTranslations('chat.contextWindow')
  const hasLimit = typeof contextWindow === 'number' && contextWindow > 0
  const promptTokens = usage?.prompt_tokens ?? 0
  const percent = hasLimit ? Math.min((promptTokens / (contextWindow as number)) * 100, 100) : 0
  const roundedPercent = Math.round(percent)
  const colorClass = levelColorClass(hasLimit, percent)
  const strokeDashoffset = RING_CIRCUMFERENCE * (1 - percent / 100)

  const ring = (
    <span className={cn('inline-flex items-center gap-1.5', colorClass)}>
      <svg viewBox="0 0 16 16" className="size-3.5 -rotate-90" aria-hidden focusable="false">
        <circle
          cx="8"
          cy="8"
          r={RING_RADIUS}
          fill="none"
          strokeWidth={2.5}
          stroke="currentColor"
          className="text-border"
          {...(hasLimit ? {} : { strokeDasharray: '2 2' })}
        />
        {hasLimit ? (
          <circle
            cx="8"
            cy="8"
            r={RING_RADIUS}
            fill="none"
            strokeWidth={2.5}
            stroke="currentColor"
            strokeLinecap="round"
            strokeDasharray={RING_CIRCUMFERENCE}
            style={{ strokeDashoffset }}
          />
        ) : null}
      </svg>
      {hasLimit ? (
        <span className="tabular-nums">
          {formatCompactCount(promptTokens, { thousandSuffix: 'k' })} /{' '}
          {formatContextWindow(contextWindow)} · {roundedPercent}%
        </span>
      ) : (
        <span className="text-muted-foreground/50">{t('disabledShort')}</span>
      )}
    </span>
  )

  return (
    <Tooltip>
      <TooltipTrigger
        render={(triggerProps) => {
          const { className, ...props } = triggerProps
          return (
            <button
              {...props}
              type="button"
              className={cn(
                className,
                'flex max-w-full items-center gap-1.5 rounded-md px-1.5 py-0.5 moldy-ui-micro text-muted-foreground',
              )}
              aria-label={
                hasLimit
                  ? t('percentAria', { percent: roundedPercent })
                  : t('disabledAria')
              }
            >
              {modelName ? (
                <>
                  <span className="truncate font-medium text-foreground/70">{modelName}</span>
                  <span className="shrink-0 text-muted-foreground/50" aria-hidden>
                    ·
                  </span>
                </>
              ) : null}
              {ring}
            </button>
          )
        }}
      />
      <TooltipContent
        role="tooltip"
        side="top"
        align="end"
        sideOffset={6}
        className="moldy-popover block w-60 max-w-none bg-popover p-2.5 moldy-ui-caption text-popover-foreground"
      >
        {hasLimit ? (
          <>
            <div className="mb-1 flex items-center justify-between border-b pb-1.5 text-foreground">
              <span className="font-medium">{t('label')}</span>
              <span className="tabular-nums text-muted-foreground">{roundedPercent}%</span>
            </div>
            <div className="tabular-nums text-muted-foreground">
              {formatDisplayNumber(promptTokens, { locale: 'en-US' })} /{' '}
              {formatDisplayNumber(contextWindow as number, { locale: 'en-US' })}
            </div>
            {percent >= 80 ? (
              <div className={cn('mt-1.5 border-t pt-1.5', colorClass)}>{t('compactHint')}</div>
            ) : null}
          </>
        ) : (
          <div className="text-muted-foreground">{t('disabled')}</div>
        )}
      </TooltipContent>
    </Tooltip>
  )
}
