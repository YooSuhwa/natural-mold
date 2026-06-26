'use client'

import { InfoIcon } from 'lucide-react'
import { useAuiState } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { formatCostUsd } from '@/components/usage/format'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { formatDisplayNumber } from '@/lib/utils/display-format'
import type { TokenUsageBreakdown } from '@/lib/types'

interface AssistantMetadataCustom {
  usage?: TokenUsageBreakdown
}

/** ms → 짧은 표기 ("0.42s" / "5.2s" / "1m 5s"). locale 무관. */
function formatDurationMs(ms: number): string {
  const seconds = ms / 1000
  if (seconds < 1) return `${seconds.toFixed(2)}s`
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m ${s}s`
}

/**
 * Assistant 메시지 푸터의 토큰 사용량 hover 팝오버.
 *
 * - 데이터 출처: ``message.metadata.custom.usage`` (W7에서 ``convertMessage``가
 *   백엔드 ``message_end``의 4종 usage를 그대로 전달).
 * - 클릭 / 호버 둘 다 토글되도록: keyboard accessible button + onMouseEnter/Leave.
 * - 빈 usage(0/0/0/0)이거나 user 메시지면 렌더하지 않음.
 */
export function TokenUsagePopover() {
  const t = useTranslations('chat.tokenUsage')
  const usage = useAuiState(
    (s) => (s.message?.metadata?.custom as AssistantMetadataCustom | undefined)?.usage,
  )

  if (!usage) return null
  const total = usage.prompt_tokens + usage.completion_tokens
  if (total === 0) return null

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
                'flex items-center gap-1 rounded-md px-1.5 py-0.5 moldy-ui-micro transition-colors hover:bg-accent',
                'text-muted-foreground hover:text-foreground',
              )}
              aria-label={t('toggleAria')}
            >
              <InfoIcon className="size-3" />
              <span className="tabular-nums">
                {formatDisplayNumber(total, { locale: 'en-US' })}
              </span>
            </button>
          )
        }}
      />
      <TooltipContent
        role="tooltip"
        side="top"
        align="start"
        sideOffset={6}
        className="moldy-popover block w-56 max-w-none bg-popover p-2.5 moldy-ui-caption text-popover-foreground"
      >
        <div className="mb-1.5 flex items-center justify-between border-b pb-1.5 text-foreground">
          <span className="font-medium">{t('title')}</span>
          <span className="tabular-nums text-muted-foreground">
            {formatDisplayNumber(total, { locale: 'en-US' })} {t('total')}
          </span>
        </div>
        <Row label={t('input')} value={usage.prompt_tokens} />
        <Row label={t('output')} value={usage.completion_tokens} />
        <Row
          label={t('cacheCreation')}
          value={usage.cache_creation_tokens}
          muted={usage.cache_creation_tokens === 0}
        />
        <Row
          label={t('cacheRead')}
          value={usage.cache_read_tokens}
          muted={usage.cache_read_tokens === 0}
        />
        {usage.estimated_cost !== undefined && usage.estimated_cost > 0 && (
          <div className="mt-1.5 flex items-center justify-between border-t pt-1.5 text-foreground">
            <span>{t('cost')}</span>
            <span className="tabular-nums">{formatCostUsd(usage.estimated_cost)}</span>
          </div>
        )}
        {(usage.tokens_per_second !== undefined || usage.generation_ms !== undefined) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 border-t pt-1.5 tabular-nums text-muted-foreground">
            {usage.tokens_per_second !== undefined && (
              <span>{t('tokensPerSecond', { value: usage.tokens_per_second })}</span>
            )}
            {usage.generation_ms !== undefined && (
              <>
                <span aria-hidden>·</span>
                <span>{formatDurationMs(usage.generation_ms)}</span>
              </>
            )}
            {usage.ttft_ms !== undefined && (
              <>
                <span aria-hidden>·</span>
                <span>{t('ttft', { value: formatDurationMs(usage.ttft_ms) })}</span>
              </>
            )}
          </div>
        )}
      </TooltipContent>
    </Tooltip>
  )
}

function Row({ label, value, muted }: { label: string; value: number; muted?: boolean }) {
  return (
    <div
      className={cn(
        'flex items-center justify-between py-0.5',
        muted ? 'text-muted-foreground/60' : 'text-muted-foreground',
      )}
    >
      <span>{label}</span>
      <span className="tabular-nums">{formatDisplayNumber(value, { locale: 'en-US' })}</span>
    </div>
  )
}
