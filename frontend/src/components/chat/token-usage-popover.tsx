'use client'

import { useState } from 'react'
import { InfoIcon } from 'lucide-react'
import { useAssistantState } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import type { TokenUsageBreakdown } from '@/lib/types'

interface AssistantMetadataCustom {
  usage?: TokenUsageBreakdown
}

function formatNumber(n: number): string {
  return n.toLocaleString('en-US')
}

function formatCost(usd: number): string {
  if (usd === 0) return '$0'
  if (usd < 0.0001) return '<$0.0001'
  if (usd < 1) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(3)}`
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
  const [open, setOpen] = useState(false)
  const usage = useAssistantState(
    (s) => (s.message?.metadata?.custom as AssistantMetadataCustom | undefined)?.usage,
  )

  if (!usage) return null
  const total = usage.prompt_tokens + usage.completion_tokens
  if (total === 0) return null

  return (
    <span
      className="relative inline-flex items-center"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] transition-colors hover:bg-accent',
          'text-muted-foreground hover:text-foreground',
        )}
        aria-label={t('toggleAria')}
        aria-expanded={open}
      >
        <InfoIcon className="size-3" />
        <span className="tabular-nums">{formatNumber(total)}</span>
      </button>

      {open && (
        <div
          role="tooltip"
          className="absolute bottom-full left-0 z-20 mb-1 w-56 rounded-lg border bg-popover p-2.5 text-[11px] shadow-md"
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
              <span className="tabular-nums">{formatCost(usage.estimated_cost)}</span>
            </div>
          )}
        </div>
      )}
    </span>
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
