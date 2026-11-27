'use client'

import { Fragment, useId, useState } from 'react'
import { Minimize2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import {
  useConversationHistoryEvidence,
  type CompactionEvidenceTarget,
} from '@/lib/hooks/use-conversation-history-evidence'
import { cn } from '@/lib/utils'

interface CompactionSummaryProps {
  readonly className?: string
  readonly evidence?: CompactionEvidenceTarget
}

export function HistoryEvidencePanel({ target }: { readonly target: CompactionEvidenceTarget }) {
  const t = useTranslations('chat.compaction')
  const query = useConversationHistoryEvidence(target)
  if (query.isPending) return <p role="status">{t('loading')}</p>
  if (query.isError) return <p role="status">{t('unavailable')}</p>

  const pages = query.data.pages
  return (
    <div className="space-y-2">
      {pages.every((page) => page.excerpts.length === 0) && <p>{t('empty')}</p>}
      <dl className="space-y-2">
        {pages.map((page, index) => (
          <Fragment key={index === 0 ? 0 : pages[index - 1].next_offset}>
            {page.excerpts.map((excerpt, entry) => (
              <div key={entry}>
                <dt className="font-medium">{t(excerpt.role)}</dt>
                <dd className="whitespace-pre-wrap break-words text-foreground">
                  {excerpt.text}
                  {excerpt.truncated && '…'}
                </dd>
              </div>
            ))}
          </Fragment>
        ))}
      </dl>
      {query.hasNextPage && (
        <Button
          type="button"
          variant="outline"
          size="xs"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {t('more')}
        </Button>
      )}
    </div>
  )
}

export function CompactionSummary({ className, evidence }: CompactionSummaryProps) {
  const t = useTranslations('chat.compaction')
  const [expanded, setExpanded] = useState(false)
  const panelId = useId()

  return (
    <div
      className={cn('space-y-2 text-xs text-muted-foreground', className)}
      data-testid="compaction-summary"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Minimize2Icon className="size-3.5 shrink-0" aria-hidden />
        <span>{t('summary')}</span>
        {evidence && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            aria-expanded={expanded}
            aria-controls={panelId}
            onClick={() => setExpanded(!expanded)}
          >
            {t(expanded ? 'hide' : 'view')}
          </Button>
        )}
      </div>
      {evidence && (
        <div id={panelId} hidden={!expanded} className="moldy-muted-panel p-3">
          {expanded && <HistoryEvidencePanel target={evidence} />}
        </div>
      )}
    </div>
  )
}
