'use client'

import { Minimize2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

interface CompactionSummaryProps {
  readonly offloadPath?: string
  readonly className?: string
}

/**
 * Permanent inline marker shown on the assistant turn whose context was
 * auto-compacted: "이전 대화를 요약해 컨텍스트를 정리했어요 · 원본 보기".
 * "원본 보기" copies the offload file path (``/conversation_history/...``) to the
 * clipboard so the user can open it via the file tools (v1 — a dedicated viewer
 * is a follow-up).
 */
export function CompactionSummary({ offloadPath, className }: CompactionSummaryProps) {
  const t = useTranslations('chat.compaction')

  const handleCopy = async () => {
    if (!offloadPath) return
    try {
      await navigator.clipboard.writeText(offloadPath)
      toast.success(t('copied'))
    } catch {
      // Clipboard unavailable (insecure context / denied) — silently no-op.
    }
  }

  return (
    <div
      className={cn('flex items-center gap-1.5 text-xs text-muted-foreground', className)}
      data-testid="compaction-summary"
    >
      <Minimize2Icon className="size-3.5 shrink-0" aria-hidden />
      <span>{t('summary')}</span>
      {offloadPath ? (
        <>
          <span aria-hidden>·</span>
          <button
            type="button"
            onClick={handleCopy}
            className="text-primary-strong hover:underline"
          >
            {t('viewOriginal')}
          </button>
        </>
      ) : null}
    </div>
  )
}
