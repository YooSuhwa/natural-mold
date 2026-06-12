'use client'

import { InfoIcon } from 'lucide-react'
import { useAuiState } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { formatCostUsd } from '@/components/usage/format'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { TokenUsageBreakdown } from '@/lib/types'

interface AssistantMetadataCustom {
  usage?: TokenUsageBreakdown
}

function formatNumber(n: number): string {
  return n.toLocaleString('en-US')
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
              <span className="tabular-nums">{formatNumber(total)}</span>
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
            {formatNumber(total)} {t('total')}
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
      <span className="tabular-nums">{formatNumber(value)}</span>
    </div>
  )
}
