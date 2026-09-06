'use client'

import { Minimize2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'

interface CompactionSummaryProps {
  readonly className?: string
}

/**
 * Permanent inline marker shown on the assistant turn whose context was
 * auto-compacted. The opaque history id remains message metadata until an
 * authenticated history resolver and viewer exist; it is intentionally neither
 * rendered nor copied to the clipboard.
 */
export function CompactionSummary({ className }: CompactionSummaryProps) {
  const t = useTranslations('chat.compaction')

  return (
    <div
      className={cn('flex items-center gap-1.5 text-xs text-muted-foreground', className)}
      data-testid="compaction-summary"
    >
      <Minimize2Icon className="size-3.5 shrink-0" aria-hidden />
      <span>{t('summary')}</span>
    </div>
  )
}
