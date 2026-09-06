'use client'

import { AlertTriangleIcon, PinIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { usePinnedConversationSummary } from '@/lib/hooks/use-pinned-conversation-summary'

type PinnedConversationSummaryProps = {
  readonly conversationId: string | null
}

export function PinnedConversationSummary({ conversationId }: PinnedConversationSummaryProps) {
  const t = useTranslations('chat.pinnedSummary')
  const { summary, isMutating, unpin } = usePinnedConversationSummary(conversationId)
  if (summary === null) return null
  const statusLabel =
    summary.source_status === 'current'
      ? null
      : {
          changed: t('statusChanged'),
          deleted: t('statusDeleted'),
          other_branch: t('statusOtherBranch'),
        }[summary.source_status]

  return (
    <aside className="moldy-muted-panel mx-auto flex w-full max-w-3xl gap-3 px-3 py-2">
      <PinIcon className="mt-0.5 size-4 shrink-0 text-primary-strong" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="moldy-ui-micro font-medium text-foreground">{t('heading')}</p>
          {statusLabel ? (
            <span className="inline-flex items-center gap-1 moldy-ui-micro text-status-warn">
              <AlertTriangleIcon className="size-3" aria-hidden="true" />
              {statusLabel}
            </span>
          ) : null}
        </div>
        <p className="line-clamp-3 break-words whitespace-pre-wrap text-sm text-muted-foreground">
          {summary.snapshot_text}
        </p>
      </div>
      <button
        type="button"
        className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
        aria-label={t('unpin')}
        title={t('unpin')}
        disabled={isMutating}
        onClick={() => unpin()}
      >
        <XIcon className="size-3.5" aria-hidden="true" />
      </button>
    </aside>
  )
}
