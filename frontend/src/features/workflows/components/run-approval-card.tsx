'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api/client'
import { useDecideApproval, useWorkflowRunApproval } from '@/lib/hooks/use-approvals'
import type { WorkflowRunDetail } from '@/lib/types/workflow'

import { AskAnswerForm } from './ask-answer-form'

/** A waiting business approval is decided right here; a tool approval goes to the inbox. */
export function RunApprovalCard({ run }: { run: WorkflowRunDetail }) {
  const t = useTranslations('workflows.runs')
  // Waiting on a decision, or on the check of a step whose result is unknown (W4).
  const stopped = run.status === 'waiting' || run.status === 'unknown'
  const pending = useWorkflowRunApproval(run.id, run.status, run.current_node, stopped)
  const decide = useDecideApproval()
  const request = pending.data
  if (!stopped || !request) return null
  if (request.kind !== 'business') {
    return (
      <section className="moldy-status-surface moldy-status-warn flex flex-col gap-2 rounded-md p-4">
        <h2 className="text-sm font-semibold">{t('toolApprovalTitle')}</h2>
        <p className="text-sm">{t('toolApprovalHint')}</p>
        <Button
          variant="outline"
          size="sm"
          className="self-start"
          render={<Link href="/approvals" />}
        >
          {t('openInbox')}
        </Button>
      </section>
    )
  }
  const action = request.actions[0]
  const args = (action?.args ?? {}) as { title?: string; body?: string }
  const unknownCheck = action?.name === 'workflow_unknown_check'
  if (action?.name === 'workflow_ask') {
    return (
      <section className="moldy-status-surface moldy-status-warn flex flex-col gap-3 rounded-md p-4">
        <h2 className="text-sm font-semibold">{args.title || t('askTitle')}</h2>
        {args.body ? <p className="text-sm whitespace-pre-wrap">{args.body}</p> : null}
        <AskAnswerForm requestId={request.id} />
      </section>
    )
  }
  const send = (type: 'approve' | 'reject') =>
    decide.mutate(
      { id: request.id, decisions: [{ type }] },
      {
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : t('decideFailed')),
      },
    )
  return (
    <section className="moldy-status-surface moldy-status-warn flex flex-col gap-3 rounded-md p-4">
      <h2 className="text-sm font-semibold">{args.title || t('approvalTitle')}</h2>
      {args.body ? <p className="text-sm whitespace-pre-wrap">{args.body}</p> : null}
      <p className="text-xs">{t(unknownCheck ? 'unknownCheckHint' : 'businessApprovalHint')}</p>
      <div className="flex gap-2">
        <Button size="sm" disabled={decide.isPending} onClick={() => send('approve')}>
          {t(unknownCheck ? 'retryStep' : 'approve')}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={decide.isPending}
          onClick={() => send('reject')}
        >
          {t(unknownCheck ? 'failRun' : 'reject')}
        </Button>
      </div>
    </section>
  )
}
