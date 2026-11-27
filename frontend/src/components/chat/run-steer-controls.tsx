'use client'

import { useSyncExternalStore } from 'react'
import { useLocale, useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import {
  MAX_STEER_CHARS,
  useRunSteerHistory,
  useRunSteerSubmission,
} from '@/lib/hooks/use-conversation-run-steers'
import { formatDisplayDateTime } from '@/lib/utils/display-format'

function watchGoalEditor(callback: () => void) {
  window.addEventListener('moldy:goal-editor-change', callback)
  return () => window.removeEventListener('moldy:goal-editor-change', callback)
}

function editableGoalConversation(): string | null {
  return document.getElementById('conversation-goal')?.dataset.steerConversation ?? null
}

export function RunSteerHistory({ conversationId }: { readonly conversationId: string }) {
  const t = useTranslations('chat.runSteer')
  const locale = useLocale()
  const formatTime = (value: string) =>
    formatDisplayDateTime(value, { locale, format: { dateStyle: 'short', timeStyle: 'medium' } })
  const goalConversation = useSyncExternalStore(
    watchGoalEditor,
    editableGoalConversation,
    () => null,
  )
  const { history, inaccessible, hasOlder, older, newer, recent, pageDepth } =
    useRunSteerHistory(conversationId)
  if (inaccessible) return null
  const receipts = history.data ?? []
  if (!receipts.length && !history.isError && pageDepth === 0) return null

  const title = pageDepth ? t('olderHistory') : t('history')
  return (
    <section aria-label={title} className="space-y-1 border-b border-border/60 px-3 py-2">
      <p className="text-xs font-medium">{title}</p>
      {pageDepth > 0 && !receipts.length && !history.isError && (
        <p role="status" className="text-xs text-muted-foreground">
          {history.isPending ? t('loading') : t('noOlder')}
        </p>
      )}
      {history.isError && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {t('refreshFailed')}
          <Button type="button" variant="ghost" size="sm" onClick={() => void history.refetch()}>
            {t('refresh')}
          </Button>
        </div>
      )}
      <ol className="max-h-32 space-y-1 overflow-y-auto" aria-live="polite">
        {receipts.map((row) => (
          <li
            key={row.id}
            data-moldy-run-steer-status={row.status}
            className="flex flex-wrap items-baseline gap-x-2 text-xs"
          >
            <span className="font-medium">{t(row.status)}</span>
            <span className="min-w-0 break-words">{row.text}</span>
            <span className="text-muted-foreground">
              {t('receivedAt', { time: formatTime(row.created_at) })}
              {row.applied_at && ' · '}
              {row.applied_at && t('appliedAt', { time: formatTime(row.applied_at) })}
            </span>
            {goalConversation === conversationId && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  if (editableGoalConversation() !== conversationId) return
                  window.dispatchEvent(
                    new CustomEvent('moldy:review-steer-criterion', {
                      detail: { conversationId, text: row.text },
                    }),
                  )
                }}
              >
                {t('reviewCriterion')}
              </Button>
            )}
          </li>
        ))}
      </ol>
      {(hasOlder || pageDepth > 0) && (
        <div className="flex flex-wrap gap-1">
          {hasOlder && (
            <Button type="button" size="sm" variant="ghost" onClick={older}>
              {t('older')}
            </Button>
          )}
          {pageDepth > 1 && (
            <Button type="button" size="sm" variant="ghost" onClick={newer}>
              {t('newer')}
            </Button>
          )}
          {pageDepth > 0 && (
            <Button type="button" size="sm" variant="ghost" onClick={recent}>
              {t('recent')}
            </Button>
          )}
        </div>
      )}
    </section>
  )
}

export function RunSteerSend({
  conversationId,
  hasResourceContext,
  submissionPending = false,
}: {
  readonly conversationId: string
  readonly hasResourceContext: boolean
  readonly submissionPending?: boolean
}) {
  const t = useTranslations('chat.runSteer')
  const { visible, disabled, send, retryOriginal, overLimit, textOnly, errorMessage } =
    useRunSteerSubmission(conversationId, hasResourceContext)
  const reason = !textOnly
    ? t('textOnly')
    : overLimit
      ? t('tooLong', { limit: MAX_STEER_CHARS })
      : null
  return (
    <>
      {visible && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-moldy-run-steer-send
          disabled={disabled || submissionPending}
          title={reason ?? t('send')}
          onClick={send}
        >
          {retryOriginal ? t('retry') : t('send')}
        </Button>
      )}
      {visible && reason && <span className="text-xs text-muted-foreground">{reason}</span>}
      {errorMessage && (
        <span role="status" className="text-xs text-destructive">
          {t(errorMessage)}
        </span>
      )}
    </>
  )
}
