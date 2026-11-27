'use client'

import { Fragment, useEffect, useId, useState } from 'react'
import { Minimize2Icon } from 'lucide-react'
import { hashKey } from '@tanstack/react-query'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import {
  useConversationHistoryEvidence,
  type CompactionEvidenceTarget,
} from '@/lib/hooks/use-conversation-history-evidence'
import { cn } from '@/lib/utils'
import { resolveCompactionCheckpoint } from '@/lib/api/conversation-history-evidence'
import {
  MOLDY_BRANCH_SWITCHED_EVENT,
  isMoldyBranchSwitchedEvent,
} from '@/lib/chat/langgraph-runtime/branch-switch-events'

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

export function CompactionSummary(props: CompactionSummaryProps) {
  const target = props.evidence
  const origin =
    target &&
    hashKey([target.conversationId, target.messageId, target.historyId, target.cutoffIndex])
  return <CompactionDisclosure key={origin} {...props} />
}

interface DisclosureRead {
  readonly controller: AbortController
  readonly expectedCheckpoint?: string
  readonly resolvedCheckpoint?: string
  readonly failed?: boolean
}

function CompactionDisclosure({ className, evidence }: CompactionSummaryProps) {
  const t = useTranslations('chat.compaction')
  const [read, setRead] = useState<DisclosureRead | null>(null)
  const panelId = useId()
  const checkpoint = evidence?.checkpointId
  const conversationId = evidence?.conversationId
  const controller = read?.controller

  // A live message can gain its saved checkpoint while its first lookup is pending.
  // A different committed checkpoint always requires another user click.
  if (read) {
    const differentResult = Boolean(
      read.resolvedCheckpoint && checkpoint && read.resolvedCheckpoint !== checkpoint,
    )
    if (differentResult || (read.expectedCheckpoint && read.expectedCheckpoint !== checkpoint)) {
      setRead(null)
    } else if (checkpoint && !read.expectedCheckpoint) {
      setRead({ ...read, expectedCheckpoint: checkpoint })
    }
  }

  useEffect(() => () => controller?.abort(), [controller])
  useEffect(() => {
    if (!controller) return
    const closeOnBranchSwitch = (event: Event) => {
      if (isMoldyBranchSwitchedEvent(event) && event.detail.conversationId === conversationId) {
        controller.abort()
        setRead(null)
      }
    }
    window.addEventListener(MOLDY_BRANCH_SWITCHED_EVENT, closeOnBranchSwitch)
    return () => window.removeEventListener(MOLDY_BRANCH_SWITCHED_EVENT, closeOnBranchSwitch)
  }, [controller, conversationId])

  function toggle() {
    if (!evidence || read) {
      setRead(null)
      return
    }
    const attempt = new AbortController()
    setRead({ controller: attempt, expectedCheckpoint: checkpoint, resolvedCheckpoint: checkpoint })
    if (checkpoint) return
    void resolveCompactionCheckpoint(evidence, attempt.signal).then(
      (resolvedCheckpoint) => {
        setRead((current) =>
          current?.controller === attempt ? { ...current, resolvedCheckpoint } : current,
        )
      },
      () => {
        if (!attempt.signal.aborted) {
          setRead((current) =>
            current?.controller === attempt ? { ...current, failed: true } : current,
          )
        }
      },
    )
  }

  const expanded = read !== null
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
            onClick={toggle}
          >
            {t(expanded ? 'hide' : 'view')}
          </Button>
        )}
      </div>
      {evidence && (
        <div id={panelId} hidden={!expanded} className="moldy-muted-panel p-3">
          {read?.resolvedCheckpoint ? (
            <HistoryEvidencePanel target={{ ...evidence, checkpointId: read.resolvedCheckpoint }} />
          ) : read ? (
            <p role="status">{t(read.failed ? 'unavailable' : 'loading')}</p>
          ) : null}
        </div>
      )}
    </div>
  )
}
