'use client'

import { ArrowDownToLineIcon, ArrowUpFromLineIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatCompactCount, formatDisplayUsd } from '@/lib/utils/display-format'
import type { TokenUsage } from '@/lib/stores/chat-store'

export function formatComposerCost(value: number): string {
  const decimals = value < 0.01 ? 4 : 2
  return formatDisplayUsd(value, {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  })
}

export function TokenBar({
  tokenUsage,
  showDivider,
  className,
}: {
  readonly tokenUsage: TokenUsage
  readonly showDivider: boolean
  readonly className?: string
}) {
  return (
    <>
      {showDivider && <span className="text-border">·</span>}
      <span className={cn('flex items-center gap-1', className)}>
        <ArrowDownToLineIcon className="size-3" />
        {formatCompactCount(tokenUsage.inputTokens, { thousandSuffix: 'k' })}
      </span>
      <span className="flex items-center gap-1">
        <ArrowUpFromLineIcon className="size-3" />
        {formatCompactCount(tokenUsage.outputTokens, { thousandSuffix: 'k' })}
      </span>
      {tokenUsage.cost > 0 && (
        <>
          <span className="text-border">·</span>
          <span>{formatComposerCost(tokenUsage.cost)}</span>
        </>
      )}
    </>
  )
}
