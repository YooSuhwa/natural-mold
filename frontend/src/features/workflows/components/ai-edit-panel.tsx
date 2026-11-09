'use client'

import { useState } from 'react'
import { SendIcon, SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import { useWorkflowAssist } from '@/lib/hooks/use-workflows'
import type { WorkflowAssistResponse, WorkflowAssistTurn } from '@/lib/types/workflow'
import { cn } from '@/lib/utils'

/** The proposal the editor holds: plan first for a large change, then the canvas diff. */
export interface PendingProposal {
  response: WorkflowAssistResponse
  reviewing: boolean
}

/**
 * The editor's "AI" tab (workflow-plan 3.7, W5): the person asks, the model asks back or
 * proposes a change. A proposal is never saved here; the editor shows it as a diff and the
 * person applies or discards all of it.
 */
export function AiEditPanel({
  workflowId,
  revision,
  canSend,
  proposal,
  onProposal,
  onReview,
}: {
  workflowId: string
  revision: number
  canSend: boolean
  proposal: PendingProposal | null
  onProposal: (response: WorkflowAssistResponse) => void
  onReview: () => void
}) {
  const t = useTranslations('workflows.ai')
  const assist = useWorkflowAssist(workflowId)
  const [turns, setTurns] = useState<WorkflowAssistTurn[]>([])
  const [draft, setDraft] = useState('')
  const busy = assist.isPending || proposal !== null
  const send = () => {
    const message = draft.trim()
    if (!message || busy || !canSend) return
    assist.mutate(
      { revision, message, history: turns },
      {
        onSuccess: (response) => {
          setTurns((current) => [
            ...current,
            { role: 'user', text: message },
            { role: 'assistant', text: response.message },
          ])
          setDraft('')
          if (response.kind === 'proposal' && response.definition) onProposal(response)
        },
        onError: (error) => toast.error(error instanceof ApiError ? error.message : t('failed')),
      },
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-muted-foreground">{t('privacy')}</p>
      {turns.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <ol className="flex flex-col gap-2" aria-label={t('conversation')}>
          {turns.map((turn, index) => (
            <li
              key={index}
              className={cn(
                'rounded-md px-3 py-2 text-sm whitespace-pre-wrap',
                turn.role === 'user' ? 'moldy-muted-panel self-end' : 'border',
              )}
            >
              <span className="mb-1 block text-xs text-muted-foreground">
                {t(turn.role === 'user' ? 'you' : 'assistant')}
              </span>
              {turn.text}
            </li>
          ))}
        </ol>
      )}
      {proposal ? (
        <div
          className="flex flex-col gap-2 rounded-md border p-3"
          role="region"
          aria-label={t('planTitle')}
        >
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <SparklesIcon className="size-4" aria-hidden />
            {t('planTitle')}
          </h3>
          {proposal.response.plan.length > 0 ? (
            <ol className="list-decimal pl-5 text-sm">
              {proposal.response.plan.map((step, index) => (
                <li key={index}>{step}</li>
              ))}
            </ol>
          ) : null}
          {proposal.reviewing ? (
            <p className="text-xs text-muted-foreground">{t('reviewHint')}</p>
          ) : (
            <Button size="sm" className="self-start" onClick={onReview}>
              {t('review')}
            </Button>
          )}
        </div>
      ) : null}
      <div className="flex flex-col gap-2">
        <Textarea
          rows={3}
          maxLength={4000}
          value={draft}
          aria-label={t('input')}
          placeholder={t('placeholder')}
          disabled={busy}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) send()
          }}
        />
        {!canSend ? <p className="text-xs text-muted-foreground">{t('needsSave')}</p> : null}
        <Button
          size="sm"
          className="self-end"
          disabled={busy || !canSend || !draft.trim()}
          onClick={send}
        >
          <SendIcon className="size-4" aria-hidden />
          {assist.isPending ? t('sending') : t('send')}
        </Button>
      </div>
    </div>
  )
}
